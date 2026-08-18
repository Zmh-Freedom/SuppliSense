"""Unit contracts for display-safe, durable Agent trace events."""

import pytest

from app.domains.agent_run import service
from app.graphs.agent_core.trace import build_agent_trace_events, sanitize_graph_trace_payload


def test_trace_events_cover_supervisor_lifecycle_without_private_reasoning() -> None:
    plan_events = build_agent_trace_events("planning", "PLANNING", {
        "plan": {"tasks": [{"task_id": "supplier-a:risk", "agent": "risk"}]},
        "analysis_scope": {
            "target_supplier_names": ["供应商 A"],
            "analysis_dimensions": ["risk"],
        },
        "reasoning": "private model chain",
    })
    result_events = build_agent_trace_events("agent_result", "EXECUTING", {
        "task_id": "supplier-a:risk",
        "result": {
            "agent": "risk", "status": "completed", "reasoning": "do not persist",
            "metrics": {"duration_ms": 42, "attempts": 1, "evidence_count": 2},
        },
    })
    evidence_events = build_agent_trace_events("evidence_merge", "EVIDENCE_MERGING", {
        "evidence_loop": {"iteration": 1, "max_iterations": 2, "tool_call_count": 1, "stop_reason": "complete"},
        "evidence_validation": {"status": "completed", "coverage": 1, "can_recommend": True, "stop_reason": "complete"},
        "evidence_merge": {"evidence": [{"evidence_id": "e-1"}]},
    })
    approval_events = build_agent_trace_events("approval_required", "WAITING_HUMAN_APPROVAL", {
        "pending_approvals": [{"approval_id": "a-1", "action_type": "add_watchlist"}],
    })
    approval_events += build_agent_trace_events("approval", "DECISION_READY", {
        "approved": True, "status": "approved", "pending_approvals": [{"approval_id": "a-1"}],
    })
    final_events = build_agent_trace_events("done", "COMPLETED", {"final_answer": "模型结论"})

    kinds = [event["kind"] for event in [*plan_events, *result_events, *evidence_events, *approval_events, *final_events]]
    assert {"route_selected", "plan_created", "subtask_completed", "tool_summary", "loop_evaluated", "validator_completed", "evidence_referenced", "approval_required", "approval_resolved", "run_completed"}.issubset(kinds)
    assert result_events[1]["data"] == {
        "task_id": "supplier-a:risk", "agent": "risk", "result_status": "completed",
        "duration_ms": 42, "attempts": 1, "evidence_count": 2,
    }
    assert "reasoning" not in str([*plan_events, *result_events])
    assert plan_events[1]["data"]["target_supplier_names"] == ["供应商 A"]
    assert plan_events[1]["data"]["analysis_dimensions"] == ["risk"]
    assert "final_answer" not in final_events[0]["data"]


def test_v2_graph_trace_filters_raw_node_output_and_private_fields() -> None:
    payload = sanitize_graph_trace_payload({
        "node": "investigate_parallel", "status": "INVESTIGATING",
        "output": {"reasoning": "private"}, "messages": ["private"],
        "reasoning": "private", "count": 2,
    })

    assert payload == {"node": "investigate_parallel", "status": "INVESTIGATING", "count": 2}


def test_persist_supervisor_snapshot_appends_trace_events_in_same_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(service, "get_cursor", _no_cursor)
    monkeypatch.setattr(service, "get_orchestration_run_for_update", lambda *_: _run())
    monkeypatch.setattr(
        service,
        "append_event",
        lambda _run_id, _version, event_type, payload, **_: events.append((event_type, payload)) or {"event_id": len(events)},
    )

    event_id = service.persist_supervisor_snapshot(
        "run-id", "EXECUTING", "agent_start", {"task_id": "supplier-a:risk", "agent": "risk"},
    )

    assert event_id == 1
    assert events[0][0] == "agent_start"
    assert events[1] == ("agent_trace", {
        "schema_version": 1,
        "kind": "subtask_started",
        "status": "EXECUTING",
        "message": "已开始执行子任务",
        "data": {"task_id": "supplier-a:risk", "agent": "risk"},
    })


def _run() -> dict:
    return {"id": "run-id", "status": "INVESTIGATING", "version": 4}


def _no_cursor():
    class CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, *_):
            return False

    return CursorContext()
