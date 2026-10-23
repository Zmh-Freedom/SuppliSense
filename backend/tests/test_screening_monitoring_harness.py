"""Agent E2E coverage for current screening and monitoring evidence flows."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core.deps import get_current_user
from app.domains.alert import coverage_service, intake_service
from app.domains.alert.tools import investigate_supplier_monitoring
from app.domains.risk import api_screening, sanctions_service, sanctions_sources
from app.domains.risk.tools_analysis import check_sanctions
from app.graphs.harness import run_harness
from app.schemas.screening import ScreeningReadResponse
from app.tools.contracts import TOOL_OUTPUT_MODELS
from app.tools.executor import ToolContext, ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec
from tests.evidence_validity_fixtures import ENTITY_ID, NOW, SCOPE, source_evidence


pytestmark = pytest.mark.agent_e2e

COMPANY_NAME = "虚构甲供应链有限公司"
ACTOR_ID = "buyer-fixture-a"
UNIFIED_CODE = "fixture-unified-code-a"
SOURCE = "fixture-formal-sanctions-list"


def _screening_identity() -> dict:
    return {
        "company_id": ENTITY_ID,
        "company_name": COMPANY_NAME,
        "unified_social_credit_code": UNIFIED_CODE,
    }


def _screening_contract() -> dict:
    return {
        "source": SOURCE,
        "source_version": "fixture-v1",
        "list_name": "虚构测试名单",
        "authority": "隔离测试来源",
        "list_type": "fixture",
        "scope": SCOPE,
        "required": True,
        "allowed_match_identifiers": ["unified_social_credit_code"],
    }


def _install_screening_boundary(monkeypatch, scenario: str) -> None:
    """Keep the real screening service while replacing identity and source I/O."""
    identity = _screening_identity()
    contract = _screening_contract()
    monkeypatch.setattr(
        sanctions_service,
        "get_assessment_context",
        lambda: {
            "actor_id": ACTOR_ID,
            "actor_role": "purchaser",
            "company_id": ENTITY_ID,
            "scope": SCOPE,
        },
    )
    monkeypatch.setattr(sanctions_service, "resolve_identity", lambda *_args, **_kwargs: identity)
    monkeypatch.setattr(sanctions_service, "read_domestic_findings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(sanctions_service, "_utc_now", lambda _value: NOW)

    if scenario == "unavailable":
        monkeypatch.setattr(sanctions_sources, "get_sanctions_source_contracts", lambda: [])
        return

    if scenario == "no_match":
        status = "empty"
        receipt = {"status": "success", "record_count": 0}
        matches: list[dict] = []
    else:
        status = "available"
        receipt = {"status": "success", "record_count": 1}
        if scenario == "possible":
            matches = [{
                "record_id": "fixture-record-possible",
                "name": "同名线索主体有限公司",
                "aliases": [COMPANY_NAME],
                "identifiers": {},
            }]
        elif scenario == "confirmed":
            matches = [{
                "record_id": "fixture-record-confirmed",
                "name": COMPANY_NAME,
                "identifiers": {"unified_social_credit_code": UNIFIED_CODE},
            }]
        else:
            raise ValueError(f"unknown screening scenario: {scenario}")

    evidence = source_evidence(
        source=SOURCE,
        source_version="fixture-v1",
        entity_id=ENTITY_ID,
        scope=SCOPE,
        status=status,
        query_receipt=receipt,
    )
    result = {"evidence": evidence, "matches": matches}
    monkeypatch.setattr(sanctions_sources, "get_sanctions_source_contracts", lambda: [contract])
    monkeypatch.setattr(
        sanctions_sources,
        "load_sanctions_source",
        lambda _contract, _identity: deepcopy(result),
    )


def _registry(*tools) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(
            tool,
            ToolSpec(
                name=tool.name,
                capability="compliance" if tool.name == "check_sanctions" else "risk_monitoring",
                side_effect="read",
                approval_policy="none",
                evidence_required=True,
            ),
            output_model=TOOL_OUTPUT_MODELS[tool.name],
        )
    return registry


def run_screening_harness(monkeypatch, *, scenario: str) -> dict:
    """Invoke the production tool, executor, and Harness over isolated sources."""
    _install_screening_boundary(monkeypatch, scenario)
    current_task = {
        "task_id": f"screening-{scenario}",
        "task_type": "analysis",
        "target_supplier_names": [COMPANY_NAME],
        "analysis_dimensions": ["compliance"],
        "user_message": f"查询{COMPANY_NAME}合规名单筛查",
    }
    execution_context = {
        "actor_id": ACTOR_ID,
        "actor_role": "purchaser",
        "scope": SCOPE,
        "references": [{
            "name": COMPANY_NAME,
            "company_id": ENTITY_ID,
            "supplier_id": "supplier-fixture-a",
        }],
        "current_task": current_task,
    }
    persisted: list[tuple[str, dict]] = []

    async def persist(event: str, state: dict) -> None:
        persisted.append((event, state))

    result = asyncio.run(run_harness(
        {
            "session_id": f"screening-session-{scenario}",
            "turn_id": f"screening-turn-{scenario}",
            "run_id": f"screening-run-{scenario}",
            "user_message": current_task["user_message"],
            "execution_context": execution_context,
            "current_task": current_task,
            "budget": {"max_tool_calls": 3, "max_loop_iterations": 0},
        },
        executor=ToolExecutor(_registry(check_sanctions)),
        persist=persist,
    ))
    result["_persist_events"] = [event for event, _state in persisted]
    return result


def test_no_source_harness_does_not_claim_screening_complete(monkeypatch):
    result = run_screening_harness(monkeypatch, scenario="unavailable")
    answer = result["answer"]

    assert answer["status"] != "completed"
    assert not any(claim["fact_path"] == "clean" for claim in answer["claims"])
    assert "已完成" not in answer["summary"]
    assert "无法判断" in answer["summary"]
    assert "unavailable" in [record["facts"]["screening_status"] for record in result["evidence_records"]]
    assert result["_persist_events"].count("load_session") == 1
    assert result["_persist_events"].count("resolve_turn") == 1


@pytest.mark.parametrize(
    ("scenario", "expected_status", "summary_text"),
    [
        ("possible", "possible_match", "名称相似"),
        ("confirmed", "confirmed_match", "主体标识一致"),
        ("no_match", "no_match_in_scope", "已核验的名单范围内"),
    ],
)
def test_formal_screening_states_reach_harness_answer(monkeypatch, scenario, expected_status, summary_text):
    result = run_screening_harness(monkeypatch, scenario=scenario)
    records = result["evidence_records"]
    screening_states = {record["facts"].get("screening_status") for record in records}

    assert expected_status in screening_states
    assert summary_text in result["answer"]["summary"]
    if scenario == "no_match":
        assert any(claim["fact_path"] == "clean" and claim["value"] is True for claim in result["answer"]["claims"])
    else:
        assert not any(claim["fact_path"] == "clean" for claim in result["answer"]["claims"])


def test_unauthorized_screening_tool_is_denied_before_source_read(monkeypatch):
    source_reads: list[str] = []
    monkeypatch.setattr(
        sanctions_sources,
        "get_sanctions_source_contracts",
        lambda: source_reads.append("contracts") or [],
    )
    executor = ToolExecutor(_registry(check_sanctions))

    outcome = asyncio.run(executor.execute(
        "check_sanctions",
        {"company_name": COMPANY_NAME},
        ToolContext(allowed_tools=set()),
    ))

    assert outcome.status == "denied"
    assert source_reads == []


class _MemoryCollection:
    def __init__(self, count: int = 0):
        self.count = count

    def count_documents(self, _query: dict) -> int:
        return self.count


class _MemoryDatabase:
    def __getitem__(self, name: str) -> _MemoryCollection:
        return _MemoryCollection(count=2 if name == "internal_supplier_material_relations" else 0)


def _monitoring_harness(monkeypatch) -> dict:
    candidate = {
        "candidate_id": "supplier:supplier-fixture-a",
        "candidate_type": "supplier",
        "company_id": ENTITY_ID,
        "supplier_id": "supplier-fixture-a",
        "supplier_code": "S-A",
        "legal_name": COMPANY_NAME,
        "verification_status": "verified",
        "confidence": 1.0,
    }
    monkeypatch.setattr(intake_service, "_load_local_candidates", lambda _query: [candidate])
    monkeypatch.setattr(
        intake_service,
        "_load_external_profile",
        lambda _query: (None, {"key": "enterprise", "status": "unavailable", "detail": "隔离验收中未查询外部工商源"}),
    )
    monkeypatch.setattr(intake_service, "get_db", _MemoryDatabase)
    monkeypatch.setattr(
        coverage_service,
        "read_transaction_validity",
        lambda *_args, **_kwargs: {
            "source": "supplier_transaction_snapshots",
            "status": "stale",
            "reason_codes": ["SNAPSHOT_EXPIRED"],
            "detail": "最近交易快照已过期，需要重新核验后才能代表当前合作情况。",
            "facts": {"snapshot_month": "2025-03", "observed_at": "2025-04-01T00:00:00Z"},
        },
    )
    monkeypatch.setattr(
        coverage_service,
        "read_sentiment_validity",
        lambda *_args, **_kwargs: {
            "source": "sentiment_results",
            "status": "missing",
            "reason_codes": ["SENTIMENT_SOURCE_MISSING"],
            "detail": "当前没有舆情分析记录。",
            "facts": {},
        },
    )
    monkeypatch.setattr("app.domains.risk.repo_financial.get_financial_metrics", lambda _name: None)

    task = {
        "task_id": "monitoring-intake",
        "task_type": "analysis",
        "target_supplier_names": [COMPANY_NAME],
        "analysis_dimensions": [],
        "user_message": f"调查{COMPANY_NAME}监控资料，说明哪些历史资料不足以及下一步核验事项",
    }
    context = {
        "references": [{"name": COMPANY_NAME, "company_id": ENTITY_ID, "supplier_id": "supplier-fixture-a"}],
        "scope": SCOPE,
        "current_task": task,
    }
    return asyncio.run(run_harness(
        {
            "session_id": "monitoring-session-fixture",
            "turn_id": "monitoring-turn-fixture",
            "run_id": "monitoring-run-fixture",
            "user_message": task["user_message"],
            "execution_context": context,
            "current_task": task,
            "budget": {"max_tool_calls": 3, "max_loop_iterations": 0},
        },
        executor=ToolExecutor(_registry(investigate_supplier_monitoring)),
    ))


def test_monitoring_harness_preserves_stale_coverage_and_next_verification(monkeypatch):
    result = _monitoring_harness(monkeypatch)
    outcome = next(item for item in result["tool_outcomes"] if item["tool_name"] == "investigate_supplier_monitoring")
    data = outcome["data"]
    dimensions = {item["key"]: item for item in data["data_coverage"]["dimensions"]}

    assert dimensions["transaction"]["validity"]["status"] == "stale"
    assert dimensions["transaction"]["status"] == "pending"
    assert "重新核验" in dimensions["transaction"]["detail"]
    assert data["data_coverage"]["missing_dimensions"]
    assert any("不表示当前供货" in item["evidence"] for item in data["findings"])
    assert "低风险" not in result["answer"]["summary"]


def test_screening_api_uses_real_service_with_memory_source(monkeypatch):
    _install_screening_boundary(monkeypatch, "no_match")
    app = FastAPI()
    app.include_router(api_screening.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=ACTOR_ID,
        role=SimpleNamespace(value="purchaser"),
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/sanctions-screenings/{COMPANY_NAME}")

    assert response.status_code == 200
    body = ScreeningReadResponse.model_validate(response.json())
    assert body.screening_status == "no_match_in_scope"
    assert body.coverage["status"] == "complete"
    assert body.coverage["sources"][0]["source"] == SOURCE


def public_screening_fixture(monkeypatch) -> dict:
    """Generate the frontend contract from a real Harness run, not by hand."""
    result = run_screening_harness(monkeypatch, scenario="possible")
    evidence = deepcopy(result["evidence_records"])
    for record in evidence:
        record["collected_at"] = "2026-10-22T00:05:00Z"
    return {"answer": result["answer"], "evidence": evidence}


def test_actual_harness_contract_matches_frontend_fixture(monkeypatch):
    import json
    from pathlib import Path

    actual = public_screening_fixture(monkeypatch)
    fixture = (
        Path(__file__).resolve().parents[2]
        / "frontend/src/__tests__/fixtures/screeningMonitoringAnswer.json"
    )

    assert fixture.exists(), "Generate fixture from public_screening_fixture, not by hand"
    assert actual == json.loads(fixture.read_text())
