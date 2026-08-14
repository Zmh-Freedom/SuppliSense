"""Graph-level behavior tests for the durable Agent Supervisor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, call

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.domains.agent_run import service as agent_run_service
from app.graphs import approval
from app.graphs.agent_supervisor import graph as supervisor_graph
from app.graphs.agent_supervisor.contracts import AgentResult, PlannerTask, TaskPlan


@pytest.fixture(autouse=True)
def enable_supervisor_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor_graph.settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(supervisor_graph, "require_v2_execution", lambda *_: None)


def _plan() -> TaskPlan:
    return TaskPlan(tasks=[PlannerTask(task_id="sourcing", agent="sourcing")])


def _result_with_pending_action() -> AgentResult:
    return AgentResult(
        agent="sourcing",
        status="completed",
        summary="找到一个外部候选供应商。",
        recommended_actions=[
            {
                "action_type": "import_external_supplier",
                "target": {"company_name": "外部供应商 A"},
                "reason": "候选供应商满足采购条件",
                "impact": "写入供应商主库",
            }
        ],
    )


def _state(run_id: str) -> dict:
    return {
        "run_id": run_id,
        "user_query": "寻找供应商并评估风险",
        "intent": {"requirement": {"category": "电机"}},
    }


def _install_graph_doubles(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str, dict]]:
    events: list[tuple[str, str, dict]] = []

    async def run_ready_tasks(_plan: TaskPlan, _state: dict) -> dict[str, AgentResult]:
        return {"sourcing": _result_with_pending_action()}

    monkeypatch.setattr(supervisor_graph, "plan_agent_task", lambda *_: _plan())
    monkeypatch.setattr(supervisor_graph, "run_ready_tasks", run_ready_tasks)
    monkeypatch.setattr(
        agent_run_service,
        "create_supervisor_action_proposals",
        lambda _run_id, approvals: [
            {**approval, "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
            for approval in approvals
        ],
    )
    monkeypatch.setattr(
        agent_run_service, "approve_supervisor_action_proposal", lambda *_: None
    )
    monkeypatch.setattr(
        agent_run_service,
        "persist_supervisor_snapshot",
        lambda run_id, task_status, event_type, snapshot: events.append(
            (task_status, event_type, snapshot)
        )
        or len(events),
    )
    return events


def test_supervisor_pauses_before_mutating_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing the approval interrupt would let a recommendation reach finalization unreviewed."""
    events = _install_graph_doubles(monkeypatch)
    write_boundary = Mock(side_effect=AssertionError("write before approval"))
    monkeypatch.setattr(
        agent_run_service, "execute_supervisor_approved_action", write_boundary
    )
    graph = supervisor_graph.build_agent_supervisor_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "pause-run"}}

    result = asyncio.run(graph.ainvoke(_state("pause-run"), config))

    assert result["task_status"] == "WAITING_HUMAN_APPROVAL"
    assert result["pending_approvals"]
    assert result["__interrupt__"]
    assert result["__interrupt__"][0].value["type"] == "approval"
    assert [event_type for _, event_type, _ in events] == [
        "stage",
        "planning",
        "agent_start",
        "agent_result",
        "evidence_merge",
        "decision_ready",
        "approval_required",
    ]
    write_boundary.assert_not_called()


@pytest.mark.parametrize(
    "resume_payload",
    [
        {"approved": False, "reason": "不纳入"},
        {"approved": True, "status": "rejected", "reason": "审批记录拒绝"},
        {"approved": True, "status": "expired", "reason": "审批已过期"},
    ],
)
def test_supervisor_resume_without_valid_approval_never_writes(
    monkeypatch: pytest.MonkeyPatch, resume_payload: dict
) -> None:
    """Rejected or expired approvals must complete without entering a business write boundary."""
    _install_graph_doubles(monkeypatch)
    write_boundary = Mock(side_effect=AssertionError("rejected approval wrote data"))
    monkeypatch.setattr(
        agent_run_service, "execute_supervisor_approved_action", write_boundary
    )
    graph = supervisor_graph.build_agent_supervisor_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "reject-run"}}
    asyncio.run(graph.ainvoke(_state("reject-run"), config))

    result = asyncio.run(graph.ainvoke(Command(resume=resume_payload), config))

    assert result["task_status"] == "COMPLETED"
    assert result["pending_approvals"][0]["status"] in {"rejected", "expired"}
    write_boundary.assert_not_called()


def test_supervisor_explicit_approval_uses_existing_approved_action_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An approved resume must use the guarded V2 action boundary instead of a direct repository write."""
    _install_graph_doubles(monkeypatch)
    write_boundary = Mock(return_value=None)
    monkeypatch.setattr(
        agent_run_service, "execute_supervisor_approved_action", write_boundary
    )
    graph = supervisor_graph.build_agent_supervisor_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "approve-run"}}
    paused = asyncio.run(graph.ainvoke(_state("approve-run"), config))

    result = asyncio.run(
        graph.ainvoke(
            Command(resume={"approved": True, "reason": "同意纳入"}), config
        )
    )

    assert result["task_status"] == "COMPLETED"
    write_boundary.assert_called_once_with(
        "approve-run", paused["pending_approvals"][0]["approval_id"]
    )


def test_supervisor_approval_uses_persisted_action_proposal_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph must execute the proposal ID returned by the durable action boundary."""
    _install_graph_doubles(monkeypatch)
    monkeypatch.setattr(
        agent_run_service,
        "create_supervisor_action_proposals",
        lambda *_: [{
            "approval_id": "proposal-1",
            "action_type": "import_external_supplier",
            "target": {"company_name": "外部供应商 A"},
            "reason": "候选供应商满足采购条件",
            "impact": "写入供应商主库",
            "status": "pending",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "requires_approval": True,
        }],
    )
    write_boundary = Mock(return_value=None)
    monkeypatch.setattr(
        agent_run_service, "execute_supervisor_approved_action", write_boundary
    )
    graph = supervisor_graph.build_agent_supervisor_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "proposal-run"}}

    paused = asyncio.run(graph.ainvoke(_state("proposal-run"), config))
    asyncio.run(graph.ainvoke(Command(resume={"approved": True}), config))

    assert paused["pending_approvals"][0]["approval_id"] == "proposal-1"
    write_boundary.assert_called_once_with("proposal-run", "proposal-1")


def test_supervisor_rejects_past_persisted_expiration_even_if_client_approves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale persisted expiry must override an approval flag from the resume client."""
    monkeypatch.setattr(
        "langgraph.types.interrupt",
        lambda *_: {"approved": True, "status": "approved"},
    )
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    result = approval.request_supervisor_approval(
        [{"approval_id": "proposal-1", "expires_at": expired}]
    )

    assert result["approved"] is False
    assert result["status"] == "expired"


def test_start_and_resume_reuse_durable_run_and_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Using a process-local graph would make restart recovery lose the supervisor interrupt."""
    compiled_graph = Mock()
    compiled_graph.ainvoke = AsyncMock(return_value={"task_status": "COMPLETED"})
    checkpointer = object()
    get_checkpointer = AsyncMock(return_value=checkpointer)
    build_graph = Mock(return_value=compiled_graph)
    monkeypatch.setattr(
        supervisor_graph,
        "get_orchestration_run",
        lambda run_id: {
            "id": run_id,
            "requirement": {
                "requirement_text": "寻找电机供应商并评估风险",
                "intent": {"requirement": {"category": "电机"}},
            },
        },
    )
    monkeypatch.setattr(
        supervisor_graph, "get_sourcing_risk_checkpointer", get_checkpointer
    )
    monkeypatch.setattr(
        supervisor_graph, "build_agent_supervisor_graph", build_graph
    )

    asyncio.run(supervisor_graph.start_agent_supervisor("durable-run"))
    asyncio.run(
        supervisor_graph.resume_agent_supervisor(
            "durable-run", {"approved": False, "reason": "拒绝"}
        )
    )

    assert get_checkpointer.await_count == 2
    assert build_graph.call_args_list == [
        call(checkpointer),
        call(checkpointer),
    ]
    start_input = compiled_graph.ainvoke.await_args_list[0].args[0]
    resume_input = compiled_graph.ainvoke.await_args_list[1].args[0]
    assert start_input == {
        "run_id": "durable-run",
        "user_query": "寻找电机供应商并评估风险",
        "intent": {"requirement": {"category": "电机"}},
    }
    assert resume_input == Command(
        resume={"approved": False, "reason": "拒绝"}
    )
