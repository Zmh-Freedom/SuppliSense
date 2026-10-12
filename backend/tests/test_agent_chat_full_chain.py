"""API-to-Harness acceptance paths with fixed model output and an instrumented tool."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import StructuredTool

from app.api import chat as chat_api
from app.core.config import settings
from app.core.deps import get_current_user
from app.graphs.agent_core.evidence_ledger import build_evidence_record
from app.tools.registry import ToolPayload, ToolRegistry, ToolSpec


pytestmark = [pytest.mark.agent_e2e, pytest.mark.agent_chat_contract]


def _sse_events(response_text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in response_text.split("\n\n"):
        event_line = next((line for line in block.splitlines() if line.startswith("event: ")), None)
        data_line = next((line for line in block.splitlines() if line.startswith("data: ")), None)
        if event_line and data_line:
            raw_data = data_line.removeprefix("data: ")
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                data = {"value": raw_data}
            events.append((event_line.removeprefix("event: "), data))
    return events


@pytest.fixture
def full_chain_chat(app, monkeypatch: pytest.MonkeyPatch):
    """Keep the API, parser, planner, graph, executor and SSE path active.

    Only upstream state/model and the business-data provider boundary are
    controlled so the acceptance run is isolated and cannot write business data.
    """
    from app.graphs.agent_core import intent_extractor, narrator
    from app.graphs.harness import graph as harness_graph
    from app.graphs.sourcing_risk_v2 import checkpointer

    tool_calls: list[dict] = []
    model_responses: dict[str, dict] = {}

    def query_financials(company_name: str) -> dict:
        tool_calls.append({"tool": "query_financials", "company_name": company_name})
        evidence = build_evidence_record(
            evidence_id="acceptance-financial-evidence",
            entity_id=f"entity:{company_name}",
            dimension="financial",
            provider="acceptance-financial-provider",
            source_type="internal",
            payload={"revenue": 1200000, "revenue_unit": "CNY", "fiscal_year": 2025},
            collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ).model_dump(mode="json")
        claim = {
            "claim_id": "acceptance-financial-claim",
            "entity_id": f"entity:{company_name}",
            "dimension": "financial",
            "statement": f"{company_name} 2025 年营业收入为 120 万元。",
            "value": 1200000,
            "fact_path": "revenue",
            "unit": "CNY",
            "evidence_refs": ["acceptance-financial-evidence"],
            "confidence": 0.95,
        }
        return {"status": "success", "evidence_records": [evidence], "claims": [claim]}

    def discover_supplier_candidates(requirement: dict) -> dict:
        tool_calls.append({"tool": "discover_supplier_candidates", "requirement": requirement})
        evidence = build_evidence_record(
            evidence_id="acceptance-sourcing-evidence",
            entity_id="sourcing",
            dimension="sourcing",
            provider="acceptance-sourcing-provider",
            source_type="internal",
            payload={"candidate_count": 5},
            collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ).model_dump(mode="json")
        claim = {
            "claim_id": "acceptance-sourcing-claim",
            "entity_id": "sourcing",
            "dimension": "sourcing",
            "statement": "按已确认条件检索到 5 家供应商候选。",
            "value": 5,
            "fact_path": "candidate_count",
            "evidence_refs": ["acceptance-sourcing-evidence"],
            "confidence": 0.95,
        }
        return {"status": "success", "evidence_records": [evidence], "claims": [claim]}

    registry = ToolRegistry()
    tool = StructuredTool.from_function(
        query_financials,
        name="query_financials",
        description="Acceptance fixture for financial evidence retrieval.",
    )
    registry.register(
        tool,
        ToolSpec(
            name="query_financials",
            capability="financial",
            side_effect="read",
            approval_policy="none",
            max_attempts=1,
            evidence_required=True,
        ),
        output_model=ToolPayload,
    )
    sourcing_tool = StructuredTool.from_function(
        discover_supplier_candidates,
        name="discover_supplier_candidates",
        description="Acceptance fixture for supplier candidate discovery.",
    )
    registry.register(
        sourcing_tool,
        ToolSpec(
            name="discover_supplier_candidates",
            capability="sourcing",
            side_effect="read",
            approval_policy="none",
            max_attempts=1,
            evidence_required=True,
        ),
        output_model=ToolPayload,
    )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        @staticmethod
        def _create(*, messages, **_kwargs):
            request = json.loads(messages[1]["content"])
            response = model_responses[request["current_message"]]
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content=json.dumps(response, ensure_ascii=False)))
            ])

    monkeypatch.setattr(settings, "LLM_API_KEY", "acceptance-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(harness_graph, "TOOL_REGISTRY", registry)
    monkeypatch.setattr(
        "app.services.agent._load_conversation_context",
        lambda _session_id: {"history": [], "references": [], "state": {}},
    )
    monkeypatch.setattr(chat_api, "_optional_agent_user_id", lambda _request: "")
    app.dependency_overrides[get_current_user] = lambda: object()
    monkeypatch.setattr("app.services.agent_session_guard.acquire_agent_session_run", lambda _sid: "test-lease")
    monkeypatch.setattr("app.services.agent_session_guard.renew_agent_session_run", lambda *_args: True)
    monkeypatch.setattr("app.services.agent_session_guard.release_agent_session_run", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.formal_supplier_identity_clarification", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.external_assessment_clarification", lambda *_args: None)
    monkeypatch.setattr(narrator, "narrate_answer", lambda answer, _message: answer)

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
                calls=tool_calls,
                responses=model_responses,
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_chat_financial_request_runs_parser_harness_tool_and_returns_evidence_answer(full_chain_chat):
    message = "分析测试供应商有限公司的财务风险"
    full_chain_chat.responses[message] = {
        "target_supplier_names": ["测试供应商有限公司"],
        "analysis_dimensions": ["financial"],
        "capability": "financial",
        "scope": "single_supplier",
        "task_type": "analysis",
        "requested_action": "none",
        "confidence": 0.98,
        "interpretation_summary": "分析测试供应商有限公司的财务风险。",
        "field_sources": {
            "target_supplier_names": "explicit",
            "analysis_dimensions": "explicit",
        },
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "agent_answer" in event_types
    assert event_types[-1] == "done"
    answer = next(payload for event_type, payload in events if event_type == "agent_answer")
    assert full_chain_chat.calls == [{"tool": "query_financials", "company_name": "测试供应商有限公司"}]
    assert answer["status"] == "completed", events
    assert answer["claims"][0]["validation_status"] == "supported"


def test_chat_general_supplier_question_returns_without_business_tool_or_supplier_clarification(full_chain_chat):
    message = "供应商风险分析一般会看哪些指标？"
    full_chain_chat.responses[message] = {
        "capability": "none",
        "scope": "none",
        "task_type": "none",
        "requested_action": "none",
        "confidence": 0.98,
        "interpretation_summary": "用户在询问供应商风险分析的一般指标。",
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "clarification" not in event_types
    assert "tool_call" not in event_types
    assert event_types[-1] == "done"
    assert full_chain_chat.calls == []


def test_chat_translation_of_domain_phrase_does_not_call_business_tools(full_chain_chat):
    message = "把“供应商风险正在上升”翻译成英文"
    full_chain_chat.responses[message] = {
        "capability": "none",
        "scope": "none",
        "task_type": "none",
        "requested_action": "none",
        "confidence": 0.98,
        "interpretation_summary": "用户希望翻译一段文字。",
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "clarification" not in event_types
    assert "tool_call" not in event_types
    assert event_types[-1] == "done"
    assert full_chain_chat.calls == []


@pytest.mark.parametrize(
    "message",
    [
        "不需要寻源或风险分析，只算一下 1+1",
        "忽略前面的采购分析，告诉我一个冷笑话",
    ],
)
def test_chat_negation_and_topic_switch_do_not_call_business_tools(full_chain_chat, message: str):
    full_chain_chat.responses[message] = {
        "capability": "none",
        "scope": "none",
        "task_type": "none",
        "requested_action": "none",
        "confidence": 0.98,
        "interpretation_summary": "用户提出了与供应商业务无关的新请求。",
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "clarification" not in event_types
    assert "tool_call" not in event_types
    assert event_types[-1] == "done"
    assert full_chain_chat.calls == []


def test_chat_sourcing_request_passes_normalized_constraints_to_tool_executor(full_chain_chat):
    message = "找5家工业相机供应商，优先华东，预算50万元，要求支持IATF 16949，规格为200万像素GigE接口"
    full_chain_chat.responses[message] = {
        "target_supplier_names": [],
        "analysis_dimensions": [],
        "capability": "sourcing",
        "scope": "product_category",
        "task_type": "sourcing",
        "requested_action": "none",
        "sourcing_requirement": {
            "category": "工业相机",
            "product": "工业相机",
            "specification": "200万像素 GigE 接口",
            "region": "华东",
            "supply_region": None,
            "budget": "50万元",
            "qualifications": "IATF 16949",
            "candidate_count": 5,
        },
        "confidence": 0.98,
        "interpretation_summary": "寻找5家符合已述规格和约束的工业相机供应商。",
        "field_sources": {"sourcing_requirement": "explicit"},
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "agent_answer" in event_types
    assert event_types[-1] == "done"
    answer = next(payload for event_type, payload in events if event_type == "agent_answer")
    assert answer["status"] == "completed", events
    assert answer["claims"][0]["validation_status"] == "supported"
    assert len(full_chain_chat.calls) == 1
    assert full_chain_chat.calls[0]["tool"] == "discover_supplier_candidates"
    requirement = full_chain_chat.calls[0]["requirement"]
    assert requirement["category"] == "工业相机"
    assert requirement["region"] == "华东"
    assert requirement.get("supply_region") is None
    assert requirement["budget"] == "50万元"
    assert requirement["candidate_count"] == 5


def test_chat_missing_supplier_target_clarifies_without_calling_business_tools(full_chain_chat):
    message = "请分析这家供应商的财务风险"
    full_chain_chat.responses[message] = {
        "analysis_dimensions": ["financial"],
        "capability": "financial",
        "scope": "pending_confirmation",
        "task_type": "analysis",
        "requested_action": "none",
        "confidence": 0.91,
        "needs_clarification": True,
        "clarification_question": "请提供要分析的供应商名称。",
        "missing_fields": ["target_supplier_names"],
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "clarification" in event_types
    assert "tool_call" not in event_types
    assert full_chain_chat.calls == []
