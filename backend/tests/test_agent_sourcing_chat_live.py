"""Opt-in real MiniMax API/SSE acceptance for sourcing chat inputs."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import StructuredTool

from app.core.config import settings
from app.core.deps import get_current_user
from app.evals.sourcing_chat_acceptance import (
    evaluate_sourcing_chat_case,
    load_sourcing_chat_cases,
    summarize_sourcing_chat_results,
)
from app.graphs.agent_core.evidence_ledger import build_evidence_record
from app.tools.contracts import SourcingCandidatesOutput
from app.tools.registry import ToolRegistry, ToolSpec


pytestmark = [pytest.mark.agent_e2e_live]
CASE_PATH = Path(__file__).parent / "evals" / "sourcing_chat_acceptance_cases.jsonl"


def _parse_sse_events(response_text: str) -> list[dict]:
    events: list[dict] = []
    event_name: str | None = None
    event_data: dict | None = None
    for line in response_text.splitlines():
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            try:
                value = json.loads(line.removeprefix("data: "))
            except json.JSONDecodeError:
                value = {"value": line.removeprefix("data: ")}
            event_data = value if isinstance(value, dict) else {"value": value}
        elif not line and event_name:
            events.append({"event": event_name, "data": event_data or {}})
            event_name = None
            event_data = None
    if event_name:
        events.append({"event": event_name, "data": event_data or {}})
    return events


def _candidate(name: str, candidate_id: str, kind: str) -> dict:
    external = kind == "external"
    return {
        "candidate_id": candidate_id,
        "supplier_name": name,
        "candidate_type": kind,
        "source": "web_search" if external else "internal_history",
        "source_type": "external" if external else "internal",
        "categories": ["测试寻源品类"],
        "main_products": "测试供货产品",
        "regions": ["华东"],
        "match_reasons": ["品类与规格匹配"],
        "website_url": "https://example.test/supplier",
        "source_title": "验收环境中的候选摘要" if external else None,
        "source_reference": "https://example.test/source" if external else None,
    }


def _sourcing_output(requirement: dict, candidate_result: str) -> dict:
    local_candidates = (
        [_candidate("测试历史合作供应商有限公司", "acceptance-local", "historical")]
        if candidate_result == "found"
        else []
    )
    external_candidates = (
        [_candidate("测试外部候选供应商有限公司", "acceptance-external", "external")]
        if candidate_result == "found"
        else []
    )
    candidates = [*local_candidates, *external_candidates]
    evidence_records = []
    claims = []
    for index, item in enumerate(candidates):
        evidence_id = f"live-sourcing-evidence-{index}"
        evidence_records.append(build_evidence_record(
            evidence_id=evidence_id,
            entity_id="sourcing",
            dimension="sourcing",
            provider=str(item["source"]),
            source_type=str(item["source_type"]),
            payload={key: value for key, value in item.items() if key != "candidate_id"},
            collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ).model_dump(mode="json"))
        claims.append({
            "claim_id": f"live-sourcing-claim-{index}",
            "entity_id": "sourcing",
            "dimension": "sourcing",
            "statement": f"{item['supplier_name']} 是本轮检索到的候选。",
            "value": item["supplier_name"],
            "fact_path": "supplier_name",
            "evidence_refs": [evidence_id],
            "confidence": 0.95,
        })
    found = bool(candidates)
    return {
        "status": "success" if found else "not_found",
        "source": "local_and_external",
        "source_order": ["local_history", "web_search"],
        "requirement": requirement,
        "candidates": candidates,
        "local_candidates": local_candidates,
        "external_candidates": external_candidates,
        "local_status": "success" if local_candidates else "not_found",
        "external_status": "success" if external_candidates else "not_found",
        "external_stop_reason": "candidate_count_reached" if external_candidates else "no_candidates",
        "external_failure_reasons": [],
        "external_loop": {},
        "evidence_records": evidence_records,
        "claims": claims,
        "message": "验收候选结果" if found else "当前条件下没有匹配候选",
    }


@pytest.fixture
def real_minimax_sourcing_chat(app, monkeypatch: pytest.MonkeyPatch):
    if os.getenv("RUN_MINIMAX_SOURCING_ACCEPTANCE") != "1":
        pytest.skip("set RUN_MINIMAX_SOURCING_ACCEPTANCE=1 to call real MiniMax through chat API/SSE")
    if not settings.LLM_API_KEY:
        pytest.fail("RUN_MINIMAX_SOURCING_ACCEPTANCE=1 requires LLM_API_KEY in backend/.env")

    from app.api import chat as chat_api
    from app.graphs.harness import graph as harness_graph
    from app.graphs.sourcing_risk_v2 import checkpointer

    tool_calls: list[dict] = []
    selected_result = {"candidate_result": "found"}

    def discover_supplier_candidates(requirement: dict) -> dict:
        tool_calls.append({"tool": "discover_supplier_candidates", "requirement": requirement})
        return _sourcing_output(requirement, selected_result["candidate_result"])

    registry = ToolRegistry()
    registry.register(
        StructuredTool.from_function(
            discover_supplier_candidates,
            name="discover_supplier_candidates",
            description="Read-only sourcing candidate fixture for real-model acceptance.",
        ),
        ToolSpec(
            name="discover_supplier_candidates",
            capability="sourcing",
            side_effect="read",
            approval_policy="none",
            max_attempts=1,
            evidence_required=True,
        ),
        output_model=SourcingCandidatesOutput,
    )

    monkeypatch.setattr(harness_graph, "TOOL_REGISTRY", registry)
    monkeypatch.setattr(
        "app.services.agent._load_conversation_context",
        lambda _session_id: {"history": [], "references": [], "state": {}},
    )
    monkeypatch.setattr(chat_api, "_optional_agent_user_id", lambda _request: "")
    monkeypatch.setattr("app.services.agent_session_guard.acquire_agent_session_run", lambda _sid: "live-acceptance")
    monkeypatch.setattr("app.services.agent_session_guard.renew_agent_session_run", lambda *_args: True)
    monkeypatch.setattr("app.services.agent_session_guard.release_agent_session_run", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.formal_supplier_identity_clarification", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.external_assessment_clarification", lambda *_args: None)
    app.dependency_overrides[get_current_user] = lambda: object()

    async def no_checkpointer():
        return None

    monkeypatch.setattr(checkpointer, "get_sourcing_risk_checkpointer", no_checkpointer)

    @asynccontextmanager
    async def no_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(
                client=client,
                tool_calls=tool_calls,
                selected_result=selected_result,
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_real_minimax_sourcing_api_sse_acceptance(real_minimax_sourcing_chat):
    cases = load_sourcing_chat_cases(CASE_PATH)
    results = []
    traces = []

    for case in cases:
        expected = case["expected"]
        real_minimax_sourcing_chat.tool_calls.clear()
        real_minimax_sourcing_chat.selected_result["candidate_result"] = expected.get("candidate_result", "found")
        response = real_minimax_sourcing_chat.client.post(
            "/api/v1/chat/stream",
            json={"message": case["message"], "session_id": str(uuid4()), "mode": "auto"},
        )
        events = _parse_sse_events(response.text)
        result = evaluate_sourcing_chat_case(
            case,
            events=events,
            tool_calls=list(real_minimax_sourcing_chat.tool_calls),
        )
        if response.status_code != 200:
            result["mismatches"]["http_status"] = {"expected": 200, "actual": response.status_code}
            result["passed"] = False
        results.append(result)
        traces.append({
            "id": case["id"],
            "http_status": response.status_code,
            "events": events,
            "tool_calls": list(real_minimax_sourcing_chat.tool_calls),
            "mismatches": result["mismatches"],
        })

    report = summarize_sourcing_chat_results(results)
    report["model"] = settings.LLM_MODEL
    report["traces"] = traces
    report_path = os.getenv("SOURCING_CHAT_LIVE_REPORT_PATH")
    if report_path:
        output_path = Path(report_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    assert report["passed"], json.dumps(report, ensure_ascii=False, indent=2)
