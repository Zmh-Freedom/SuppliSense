"""Contract and scenario tests for the unified Agent Harness Runtime."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from langchain_core.tools import StructuredTool

from app.graphs.agent_core.evidence_ledger import build_evidence_record
from app.graphs.harness import ExecutionBudget, build_harness_graph, run_harness
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec


def _registry_for(payload: dict) -> ToolRegistry:
    def fake_tool(company_name: str) -> dict:
        del company_name
        return payload

    tool = StructuredTool.from_function(
        fake_tool,
        name="fake_risk",
        description="test-only risk tool",
    )
    registry = ToolRegistry()
    registry.register(
        tool,
        ToolSpec(
            name="fake_risk",
            capability="risk",
            side_effect="read",
            approval_policy="none",
            evidence_required=True,
        ),
    )
    return registry


def _base_state(**overrides: object) -> dict:
    state = {
        "session_id": "session-1",
        "turn_id": "turn-1",
        "run_id": "run-1",
        "user_message": "分析测试供应商",
        "execution_context": {"references": []},
        "current_task": {},
        "task_specs": [
            {
                "task_id": "risk-1",
                "tool_name": "fake_risk",
                "arguments": {"company_name": "测试供应商"},
                "entity_id": "supplier-1",
                "dimension": "risk",
                "required": True,
            }
        ],
        "budget": ExecutionBudget().model_dump(mode="json"),
    }
    state.update(overrides)
    return state


def _evidence_and_claim() -> tuple[dict, dict]:
    evidence = build_evidence_record(
        evidence_id="ev-risk-1",
        entity_id="supplier-1",
        dimension="risk",
        provider="test-provider",
        source_type="internal",
        payload={"score": 0.2},
        collected_at=datetime.now(timezone.utc),
    ).model_dump(mode="json")
    claim = {
        "claim_id": "claim-risk-1",
        "entity_id": "supplier-1",
        "dimension": "risk",
        "statement": "测试供应商风险评分为 0.2",
        "value": 0.2,
        "evidence_refs": ["ev-risk-1"],
        "confidence": 0.9,
    }
    return evidence, claim


@pytest.mark.agent_e2e
def test_harness_runs_tool_and_returns_evidence_backed_answer() -> None:
    evidence, claim = _evidence_and_claim()
    executor = ToolExecutor(
        _registry_for({"status": "success", "evidence_records": [evidence], "claims": [claim]})
    )
    persisted: list[str] = []

    async def persist(event: str, snapshot: dict) -> None:
        persisted.append(event)
        assert snapshot["run_id"] == "run-1"

    result = asyncio.run(run_harness(_base_state(), executor=executor, persist=persist))

    assert result["status"] == "completed"
    assert result["answer"]["status"] == "completed"
    assert result["answer"]["claims"][0]["validation_status"] == "supported"
    assert result["tool_call_count"] == 1
    assert persisted == [
        "load_session",
        "resolve_turn",
        "build_plan",
        "execute_ready_tasks",
        "validate_evidence",
        "render_answer",
        "persist_turn",
    ]


@pytest.mark.agent_e2e
def test_harness_stops_at_review_when_evidence_is_missing() -> None:
    executor = ToolExecutor(_registry_for({"status": "success", "claims": []}))
    result = asyncio.run(run_harness(_base_state(), executor=executor))

    assert result["answer"]["status"] == "needs_review"
    assert any("risk" in item for item in result["answer"]["limitations"])
    assert result["tool_call_count"] == 1


@pytest.mark.agent_e2e
def test_harness_supports_multi_supplier_dimension_matrix() -> None:
    def multi_tool(company_name: str) -> dict:
        entity_id = {"甲供应商": "supplier-a", "乙供应商": "supplier-b"}[company_name]
        evidence = build_evidence_record(
            evidence_id=f"ev-{entity_id}",
            entity_id=entity_id,
            dimension="risk",
            provider="test-provider",
            source_type="internal",
            payload={"level": "low"},
        ).model_dump(mode="json")
        return {
            "status": "success",
            "evidence_records": [evidence],
            "claims": [
                {
                    "claim_id": f"claim-{entity_id}",
                    "entity_id": entity_id,
                    "dimension": "risk",
                    "statement": f"{company_name}风险证据有效",
                    "evidence_refs": [f"ev-{entity_id}"],
                    "confidence": 0.8,
                }
            ],
        }

    tool = StructuredTool.from_function(multi_tool, name="fake_risk", description="test")
    registry = ToolRegistry()
    registry.register(
        tool,
        ToolSpec(name="fake_risk", capability="risk", side_effect="read", approval_policy="none"),
    )
    executor = ToolExecutor(registry)
    state = _base_state(
        task_specs=[
            {
                "task_id": "risk-a",
                "tool_name": "fake_risk",
                "arguments": {"company_name": "甲供应商"},
                "entity_id": "supplier-a",
                "dimension": "risk",
            },
            {
                "task_id": "risk-b",
                "tool_name": "fake_risk",
                "arguments": {"company_name": "乙供应商"},
                "entity_id": "supplier-b",
                "dimension": "risk",
            },
        ]
    )

    result = asyncio.run(run_harness(state, executor=executor))

    assert result["answer"]["status"] == "completed"
    assert len(result["answer"]["claims"]) == 2
    assert result["tool_call_count"] == 2


@pytest.mark.agent_e2e
def test_harness_builds_read_only_sourcing_plan_from_current_task() -> None:
    evidence = build_evidence_record(
        evidence_id="ev-sourcing-1",
        entity_id="sourcing",
        dimension="sourcing",
        provider="test-provider",
        source_type="internal",
        payload={"supplier_name": "甲供应商"},
    ).model_dump(mode="json")

    def list_suppliers(limit: int = 20) -> dict:
        assert limit == 20
        return {
            "status": "success",
            "evidence_records": [evidence],
            "claims": [
                {
                    "claim_id": "claim-sourcing-1",
                    "entity_id": "sourcing",
                    "dimension": "sourcing",
                    "statement": "已读取正式供应商候选",
                    "evidence_refs": ["ev-sourcing-1"],
                    "confidence": 0.8,
                }
            ],
        }

    tool = StructuredTool.from_function(list_suppliers, name="list_formal_suppliers", description="test")
    registry = ToolRegistry()
    registry.register(
        tool,
        ToolSpec(
            name="list_formal_suppliers",
            capability="sourcing",
            side_effect="read",
            approval_policy="none",
            evidence_required=True,
        ),
    )
    state = _base_state(
        task_specs=[],
        current_task={
            "task_id": "source-1",
            "task_type": "sourcing",
            "target_supplier_names": [],
            "analysis_dimensions": [],
            "subtasks": [{"subtask_id": "source-1", "dimension": "sourcing"}],
        },
    )

    result = asyncio.run(run_harness(state, executor=ToolExecutor(registry)))

    assert result["task_specs"][0]["tool_name"] == "list_formal_suppliers"
    assert result["answer"]["status"] == "completed"


def test_harness_remediation_loop_is_bounded_and_can_fill_missing_evidence() -> None:
    evidence, claim = _evidence_and_claim()
    calls = 0

    def retryable_tool(company_name: str) -> dict:
        nonlocal calls
        del company_name
        calls += 1
        if calls == 1:
            return {"status": "success", "claims": []}
        return {"status": "success", "evidence_records": [evidence], "claims": [claim]}

    tool = StructuredTool.from_function(retryable_tool, name="fake_risk", description="test")
    registry = ToolRegistry()
    registry.register(
        tool,
        ToolSpec(name="fake_risk", capability="risk", side_effect="read", approval_policy="none"),
    )
    result = asyncio.run(
        run_harness(
            _base_state(
                remediation_specs=[
                    {
                        "task_id": "risk-remediation-1",
                        "tool_name": "fake_risk",
                        "arguments": {"company_name": "测试供应商"},
                        "entity_id": "supplier-1",
                        "dimension": "risk",
                    }
                ]
            ),
            executor=ToolExecutor(registry),
        )
    )

    assert result["answer"]["status"] == "completed"
    assert result["remediation_attempts"] == 1
    assert result["loop_iterations"] == 1
    assert result["tool_call_count"] == 2


def test_harness_requires_langgraph_thread_id_with_checkpointer() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    evidence, claim = _evidence_and_claim()
    executor = ToolExecutor(
        _registry_for({"status": "success", "evidence_records": [evidence], "claims": [claim]})
    )
    graph = build_harness_graph(executor=executor, checkpointer=MemorySaver())

    result = asyncio.run(graph.ainvoke(_base_state(), {"configurable": {"thread_id": "run-1"}}))

    assert result["answer"]["status"] == "completed"
