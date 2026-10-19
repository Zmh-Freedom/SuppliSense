"""Pure contracts for the internal execution lifecycle."""

import pytest

from app.domains.agent_run.coordinator import (
    ExecutionPermit,
    ExecutionState,
    can_transition,
    public_status,
)


@pytest.mark.parametrize("terminal", ["SUCCEEDED", "FAILED", "CANCELLED"])
def test_terminal_execution_cannot_restart(terminal):
    assert not can_transition(ExecutionState(terminal), ExecutionState.RUNNING)


def test_execution_quality_keeps_legacy_public_states():
    assert public_status(ExecutionState.SUCCEEDED, "partial") == "PARTIAL"
    assert public_status(ExecutionState.SUCCEEDED, "needs_review") == "NEEDS_REVIEW"
    assert public_status(ExecutionState.SUCCEEDED, "complete") == "COMPLETED"
    assert public_status(ExecutionState.CANCELLED) == "CANCELLED"


def test_cancel_request_cannot_become_success():
    assert not can_transition(ExecutionState.CANCEL_REQUESTED, ExecutionState.SUCCEEDED)
    assert can_transition(ExecutionState.CANCEL_REQUESTED, ExecutionState.CANCELLED)
    assert ExecutionPermit("run", "attempt", 2).fencing_token == 2


def test_paused_harness_replay_drains_and_closes(monkeypatch):
    from app.domains.agent_run import service
    from app.domains.agent_run.coordinator import run_coordinator

    monkeypatch.setattr(service, "_get_authorized_run", lambda *_args: {"status": "RUNNING"})
    monkeypatch.setattr(service, "list_events_after", lambda *_args: [{"event_id": 1, "event_type": "workflow_status", "payload": {"status": "stopped"}}])
    monkeypatch.setattr(run_coordinator, "is_paused", lambda _id: True, raising=False)
    monkeypatch.setattr(service.time, "sleep", lambda *_args: (_ for _ in ()).throw(AssertionError("paused stream must close")))
    stream = service.stream_harness_events("run", 0, "user", "purchaser")
    assert next(stream)["data"]["status"] == "stopped"
    with pytest.raises(StopIteration):
        next(stream)
