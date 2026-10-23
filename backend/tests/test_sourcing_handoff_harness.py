"""A sourcing handoff enters the real read-only Harness and keeps writes gated."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool

from app.api import chat as chat_api
from app.graphs.agent_core.answer_contract import AgentAnswer
from app.graphs.agent_core.evidence_ledger import build_evidence_record
from app.graphs.harness import ExecutionBudget, run_harness
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec


def _events(response) -> list[str]:
    async def collect() -> list[str]:
        return [event async for event in response.body_iterator]

    return asyncio.run(collect())


@pytest.mark.agent_e2e
def test_handoff_loads_one_context_snapshot_and_runs_read_only_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.sourcing_risk import handoff_service
    from app.graphs.agent_core import adapter

    session_id = "handoff-session"
    candidate_id = "candidate-1"
    company_name = "甲工厂有限公司"
    context_loads: list[str] = []
    resolver_calls: list[str] = []
    read_calls: list[str] = []
    evidence = build_evidence_record(
        evidence_id="evidence-risk-1",
        entity_id="supplier-1",
        dimension="risk",
        provider="test-provider",
        source_type="internal",
        payload={"score": 0.2},
        collected_at=datetime.now(timezone.utc),
    ).model_dump(mode="json")

    def load_once(sid: str, message: str):
        context_loads.append(sid)
        value = adapter.build_execution_context(
            session_id=sid,
            user_message=message,
            references=[{
                "name": company_name,
                "company_id": "company-1",
                "candidate_id": candidate_id,
                "identity_status": "exact",
            }],
        )
        value["current_task"].update({"task_type": "analysis", "capability": "risk", "analysis_dimensions": ["risk"]})
        value["llm_intent"] = {
            "task_type": "analysis",
            "capability": "risk",
            "requested_action": "none",
            "target_supplier_names": [company_name],
            "analysis_dimensions": ["risk"],
        }
        return value

    def resolve_once(sid: str, user_id: str, value: dict):
        resolver_calls.append(sid)
        assert user_id == ""
        return {
            **value,
            "sourcing_handoff": {
                "handoff_id": "handoff-1",
                "intent": "risk_review",
                "candidate_key": candidate_id,
            },
        }

    def read_risk(company_name: str) -> dict:
        read_calls.append(company_name)
        return {
            "status": "success",
            "evidence_records": [evidence],
            "claims": [{
                "claim_id": "claim-risk-1",
                "entity_id": "supplier-1",
                "dimension": "risk",
                "statement": "当前风险数据已核验",
                "value": 0.2,
                "evidence_refs": ["evidence-risk-1"],
                "confidence": 0.9,
            }],
        }

    registry = ToolRegistry()
    registry.register(
        StructuredTool.from_function(read_risk, name="read_risk", description="read-only risk assessment"),
        ToolSpec(name="read_risk", capability="risk", side_effect="read", approval_policy="none", evidence_required=True),
    )
    executor = ToolExecutor(registry)

    async def harness_stream(_sid, message, _preferences="", execution_context=None):
        assert execution_context is not None
        result = await run_harness({
            "session_id": session_id,
            "turn_id": "turn-1",
            "run_id": "run-1",
            "user_message": message,
            "execution_context": execution_context,
            "current_task": execution_context["current_task"],
            "task_specs": [{
                "task_id": "risk-1",
                "tool_name": "read_risk",
                "arguments": {"company_name": company_name},
                "entity_id": "supplier-1",
                "dimension": "risk",
                "required": True,
            }],
            "budget": ExecutionBudget().model_dump(mode="json"),
        }, executor=executor)
        assert result["answer"]["status"] == "completed"
        yield f"event: done\ndata: {json.dumps({'answer': result['answer']['summary'], 'status': 'completed'})}\n\n"

    monkeypatch.setattr(adapter, "load_execution_context", load_once)
    monkeypatch.setattr(handoff_service, "resolve_session_handoff", resolve_once)
    monkeypatch.setattr(chat_api, "_langgraph_harness_stream", harness_stream)
    monkeypatch.setattr("app.services.agent_session_guard.acquire_agent_session_run", lambda _sid: "run-token")
    monkeypatch.setattr("app.services.agent_session_guard.release_agent_session_run", lambda *_args: None)
    monkeypatch.setattr("app.services.agent_session_guard.renew_agent_session_run", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.formal_supplier_identity_clarification", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.review_scope_clarification", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.external_assessment_clarification", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.detect_clarification_needed", lambda *_args, **_kwargs: None)

    response = asyncio.run(chat_api.chat_stream_endpoint(
        chat_api.ChatRequest(message=f"分析{company_name}的风险", session_id=session_id, mode="auto"),
        SimpleNamespace(state=SimpleNamespace(user_id=""), headers={"x-sourcing-handoff-id": "handoff-1"}),
        sourcing_handoff_id="handoff-1",
    ))
    stream_events = _events(response)

    assert context_loads == [session_id]
    assert resolver_calls == [session_id]
    assert read_calls == [company_name]
    assert any(event.startswith("event: done") for event in stream_events)


@pytest.mark.agent_e2e
def test_monitor_proposal_without_approval_never_executes_a_write_tool() -> None:
    write_calls: list[str] = []

    def add_to_monitor(company_name: str) -> dict:
        write_calls.append(company_name)
        return {"status": "success", "receipt": {"target_id": "watch-1"}}

    registry = ToolRegistry()
    registry.register(
        StructuredTool.from_function(add_to_monitor, name="add_to_monitor", description="write to watchlist"),
        ToolSpec(name="add_to_monitor", capability="monitoring", side_effect="write", approval_policy="required"),
    )

    result = asyncio.run(run_harness({
        "session_id": "handoff-session",
        "turn_id": "turn-1",
        "run_id": "run-1",
        "user_message": "将甲工厂有限公司加入风险监控",
        "execution_context": {"sourcing_handoff": {"intent": "monitor_proposal"}},
        "current_task": {"task_type": "action_draft", "target_supplier_names": ["甲工厂有限公司"]},
        "task_specs": [{
            "task_id": "monitor-1",
            "tool_name": "add_to_monitor",
            "arguments": {"company_name": "甲工厂有限公司"},
            "entity_id": "supplier-1",
            "dimension": "monitoring",
            "required": True,
        }],
        "budget": ExecutionBudget().model_dump(mode="json"),
    }, executor=ToolExecutor(registry)))

    assert write_calls == []
    assert result["tool_outcomes"][0]["status"] == "denied"
    assert result["tool_outcomes"][0]["error"]["code"] == "approval_required"
