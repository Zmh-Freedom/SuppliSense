"""Unit contracts for durable Agent execution snapshots."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.domains.agent_run import service
from app.graphs.agent_supervisor import graph


def _run(status: str = "INVESTIGATING", version: int = 4) -> dict:
    return {
        "id": "00000000-0000-4000-8000-000000000001",
        "status": status,
        "version": version,
        "user_id": "user-id",
        "requirement": {"requirement_text": "采购工业摄像头"},
        "created_at": datetime.now(timezone.utc),
    }


def test_persist_supervisor_snapshot_embeds_recoverable_execution_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict] = []
    monkeypatch.setattr(service, "get_cursor", _no_cursor)
    monkeypatch.setattr(service, "get_orchestration_run_for_update", lambda *_: _run())
    monkeypatch.setattr(
        service,
        "append_event",
        lambda run_id, version, event_type, payload, **_: events.append(payload) or {"event_id": 12},
    )

    service.persist_supervisor_snapshot(
        "run-id",
        "EXECUTING",
        "agent_result",
        {
            "task_matrix": [{"subtask_id": "supplier-a:risk", "status": "pending"}],
            "task_id": "supplier-a:risk",
            "result": {"status": "completed", "summary": "已完成"},
            "evidence_loop": {
                "loop_type": "evidence", "iteration": 1, "max_iterations": 2,
                "tool_call_count": 1, "max_tool_calls": 2,
                "started_at": "2026-08-18T00:00:00+00:00", "timeout_seconds": 60,
                "stop_reason": "within_budget",
            },
            "evidence_validation": {"status": "passed"},
        },
    )

    snapshot = events[0]["execution_snapshot"]
    assert snapshot["task_matrix"][0]["subtask_id"] == "supplier-a:risk"
    assert snapshot["subtask_results"]["supplier-a:risk"]["status"] == "completed"
    assert snapshot["loops"]["evidence"]["iteration"] == 1
    assert snapshot["loops"]["evidence"]["stop_reason"] == "within_budget"
    assert snapshot["validators"]["evidence"]["status"] == "passed"


def test_load_execution_snapshot_resumes_only_unfinished_tasks_and_paused_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "get_orchestration_run", lambda *_: _run("ACTION_PENDING"))
    monkeypatch.setattr(
        service,
        "list_execution_snapshot_events",
        lambda *_: [
            {"payload": {"execution_snapshot": {
                "task_matrix": [
                    {"subtask_id": "supplier-a:risk", "status": "pending"},
                    {"subtask_id": "supplier-b:risk", "status": "pending"},
                ],
                "subtask_results": {
                    "supplier-a:risk": {"status": "completed", "summary": "完成"},
                    "supplier-b:risk": {"status": "failed", "summary": "超时"},
                },
                "loops": {"evidence": {"iteration": 1, "max_iterations": 2, "stop_reason": "within_budget"}},
                "validators": {"evidence": {"status": "needs_review"}},
                "pending_approvals": [
                    {"approval_id": "paused", "status": "pending", "action_type": "add_watchlist"},
                    {"approval_id": "done", "status": "approved", "action_type": "add_watchlist"},
                ],
            }}},
        ],
    )

    snapshot = service.load_execution_snapshot("run-id")

    assert snapshot["completed_subtask_ids"] == ["supplier-a:risk"]
    assert snapshot["resumable_subtasks"] == [{"subtask_id": "supplier-b:risk", "status": "pending"}]
    assert snapshot["subtask_results"]["supplier-a:risk"]["status"] == "completed"
    assert snapshot["pending_approvals"] == [
        {"approval_id": "paused", "status": "pending", "action_type": "add_watchlist"}
    ]
    assert snapshot["loops"]["evidence"]["stop_reason"] == "within_budget"


def test_load_execution_snapshot_hides_pending_actions_after_run_leaves_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "get_orchestration_run", lambda *_: _run("COMPLETED"))
    monkeypatch.setattr(
        service,
        "list_execution_snapshot_events",
        lambda *_: [{"payload": {"execution_snapshot": {
            "pending_approvals": [
                {"approval_id": "old", "status": "pending", "action_type": "add_watchlist"},
            ],
        }}}],
    )

    snapshot = service.load_execution_snapshot("run-id")

    assert snapshot["pending_approvals"] == []


def test_start_supervisor_reuses_completed_results_after_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    monkeypatch.setattr(graph, "require_v2_execution", lambda *_: None)
    monkeypatch.setattr(graph, "get_orchestration_run", lambda *_: _run())
    monkeypatch.setattr(
        graph.agent_run_service,
        "load_execution_snapshot",
        lambda *_: {
            "task_matrix": [{"task_id": "supplier-a:risk", "agent": "risk", "depends_on": [], "required": True}],
            "subtask_results": {
                "supplier-a:risk": {"status": "completed", "summary": "已完成"},
                "supplier-b:risk": {"status": "failed", "summary": "稍后重试"},
            },
            "loops": {"evidence": {"loop_type": "evidence", "iteration": 1, "max_iterations": 2, "tool_call_count": 1, "max_tool_calls": 2, "started_at": "2026-08-18T00:00:00+00:00", "timeout_seconds": 60}},
            "pending_approvals": [],
        },
    )
    monkeypatch.setattr(graph, "get_sourcing_risk_checkpointer", _async_none)
    monkeypatch.setattr(graph, "build_agent_supervisor_graph", lambda *_: _CapturingGraph(captured))

    asyncio.run(graph.start_agent_supervisor("run-id"))

    assert captured["input"]["plan"]["tasks"][0]["task_id"] == "supplier-a:risk"
    assert captured["input"]["agent_results"] == {
        "supplier-a:risk": {"status": "completed", "summary": "已完成"}
    }
    assert captured["input"]["evidence_loop"]["iteration"] == 1


def _no_cursor():
    class _CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, *_):
            return False

    return _CursorContext()


async def _async_none():
    return None


class _CapturingGraph:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    async def ainvoke(self, graph_input: dict, config: dict) -> None:
        self._captured["input"] = graph_input
        self._captured["config"] = config
