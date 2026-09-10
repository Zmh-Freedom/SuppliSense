"""Contract and scenario tests for the unified Agent Harness Runtime."""

from __future__ import annotations

import asyncio
import threading
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
    assert result["tool_call_count"] == 2
    assert result["remediation_attempts"] == 1


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


@pytest.mark.agent_e2e
def test_harness_builds_a_fallback_query_for_missing_evidence() -> None:
    evidence, claim = _evidence_and_claim()
    fallback_calls: list[str] = []

    def fallback_tool(company_name: str) -> dict:
        fallback_calls.append(company_name)
        return {
            "status": "success",
            "evidence_records": [{**evidence, "dimension": "financial"}],
            "claims": [{**claim, "dimension": "financial"}],
        }

    tool = StructuredTool.from_function(fallback_tool, name="query_financials", description="test")
    registry = ToolRegistry()
    registry.register(tool, ToolSpec(name="query_financials", capability="financial", side_effect="read", approval_policy="none"))
    registry.register(
        StructuredTool.from_function(lambda company_name: {"status": "success"}, name="fake_risk", description="test"),
        ToolSpec(name="fake_risk", capability="risk", side_effect="read", approval_policy="none"),
    )

    result = asyncio.run(run_harness(_base_state(), executor=ToolExecutor(registry)))

    assert fallback_calls == ["测试供应商"]
    assert result["remediation_attempts"] == 1
    assert result["task_specs"][-1]["tool_name"] == "query_financials"


def test_harness_requires_langgraph_thread_id_with_checkpointer() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    evidence, claim = _evidence_and_claim()
    executor = ToolExecutor(
        _registry_for({"status": "success", "evidence_records": [evidence], "claims": [claim]})
    )
    graph = build_harness_graph(executor=executor, checkpointer=MemorySaver())

    result = asyncio.run(graph.ainvoke(_base_state(), {"configurable": {"thread_id": "run-1"}}))

    assert result["answer"]["status"] == "completed"


def test_harness_enforces_parallel_limit_for_independent_entities() -> None:
    active = 0
    max_active = 0
    lock = threading.Lock()

    def parallel_tool(company_name: str) -> dict:
        nonlocal active, max_active
        del company_name
        with lock:
            active += 1
            max_active = max(max_active, active)
        import time

        time.sleep(0.05)
        with lock:
            active -= 1
        return {"status": "success", "message": "done"}

    tool = StructuredTool.from_function(parallel_tool, name="parallel_tool", description="test")
    registry = ToolRegistry()
    registry.register(tool, ToolSpec(name="parallel_tool", capability="risk", side_effect="read", approval_policy="none"))
    result = asyncio.run(
        run_harness(
            _base_state(
                task_specs=[
                    {
                        "task_id": "parallel-a",
                        "tool_name": "parallel_tool",
                        "arguments": {"company_name": "甲"},
                        "entity_id": "supplier-a",
                        "dimension": "risk",
                    },
                    {
                        "task_id": "parallel-b",
                        "tool_name": "parallel_tool",
                        "arguments": {"company_name": "乙"},
                        "entity_id": "supplier-b",
                        "dimension": "risk",
                    },
                ],
                budget={**ExecutionBudget().model_dump(mode="json"), "max_parallel_tasks": 2, "max_tool_calls": 2},
            ),
            executor=ToolExecutor(registry),
        )
    )

    assert max_active == 2
    assert result["tool_call_count"] == 2


def test_harness_respects_task_dependencies_before_parallel_scheduling() -> None:
    calls: list[str] = []

    def dependency_tool(company_name: str) -> dict:
        calls.append(company_name)
        return {"status": "success", "message": company_name}

    tool = StructuredTool.from_function(dependency_tool, name="dependency_tool", description="test")
    registry = ToolRegistry()
    registry.register(tool, ToolSpec(name="dependency_tool", capability="risk", side_effect="read", approval_policy="none"))
    result = asyncio.run(
        run_harness(
            _base_state(
                task_specs=[
                    {
                        "task_id": "dependency-a",
                        "tool_name": "dependency_tool",
                        "arguments": {"company_name": "甲"},
                        "entity_id": "supplier-a",
                        "dimension": "risk",
                    },
                    {
                        "task_id": "dependency-b",
                        "tool_name": "dependency_tool",
                        "arguments": {"company_name": "乙"},
                        "entity_id": "supplier-b",
                        "dimension": "risk",
                        "depends_on": ["dependency-a"],
                    },
                ],
                budget={**ExecutionBudget().model_dump(mode="json"), "max_parallel_tasks": 2, "max_loop_iterations": 0},
            ),
            executor=ToolExecutor(registry),
        )
    )

    assert calls == ["甲", "乙"]
    assert result["tool_call_count"] == 2


def test_harness_stops_before_tools_when_deadline_or_llm_budget_is_exhausted() -> None:
    def never_called(company_name: str) -> dict:
        del company_name
        return {"status": "success"}

    tool = StructuredTool.from_function(never_called, name="never_called", description="test")
    registry = ToolRegistry()
    registry.register(tool, ToolSpec(name="never_called", capability="risk", side_effect="read", approval_policy="none"))
    result = asyncio.run(
        run_harness(
            _base_state(
                llm_call_count=5,
                budget={**ExecutionBudget().model_dump(mode="json"), "max_llm_calls": 4},
            ),
            executor=ToolExecutor(registry),
        )
    )

    assert result["tool_call_count"] == 0
    assert result["loop_exit_reason"] == "llm_budget_exhausted"


def test_harness_planner_adds_bounded_trend_and_comparison_dependencies() -> None:
    from app.graphs.harness.graph import _build_default_plan

    tasks = _build_default_plan(
        {
            "current_task": {
                "task_id": "analysis-1",
                "user_message": "对甲公司和乙公司做风险趋势和横向对比",
                "target_supplier_names": ["甲公司", "乙公司"],
                "analysis_dimensions": ["risk"],
            },
            "execution_context": {"references": []},
        }
    )

    trend_tasks = [task for task in tasks if task.tool_name == "analyze_trend"]
    comparison = next(task for task in tasks if task.tool_name == "compare_companies")
    assert len(trend_tasks) == 2
    assert all(task.depends_on for task in trend_tasks)
    assert set(comparison.depends_on) == {task.task_id for task in tasks if task is not comparison}


def test_procurement_action_with_coverage_limit_does_not_request_user_materials() -> None:
    from app.graphs.harness.graph import _procurement_action_proposals
    from app.graphs.agent_core.answer_contract import AgentAnswer

    answer = AgentAnswer(
        status="needs_review",
        summary="财务维度当前未覆盖。",
        limitations=["财务数据当前未覆盖"],
    )

    proposal = _procurement_action_proposals(answer)[0]

    assert proposal["action_type"] == "supplement_data"
    assert proposal["label"] == "当前不建议变更采购策略"
    assert "补充" not in proposal["label"]
    assert "采购人员" not in proposal["reason"]
