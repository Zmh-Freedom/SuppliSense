"""Lifecycle contracts for durable sourcing-risk agent runs."""

from datetime import datetime, timezone

import pytest

from app.core.errors import DomainError
from app.domains.agent_run import service
from app.domains.agent_run.schemas import ClarificationRequest, CreateSourcingRiskRunRequest


def _run(status: str = "CREATED", version: int = 1, user_id: str = "user-id") -> dict:
    return {
        "id": "00000000-0000-4000-8000-000000000001",
        "user_id": user_id,
        "status": status,
        "version": version,
        "requirement": {"requirement_text": "采购工业摄像头"},
        "created_at": datetime.now(timezone.utc),
    }


def test_cancel_run_updates_only_the_expected_version(monkeypatch: pytest.MonkeyPatch):
    """Dropping the version check would let a stale client cancel a resumed run."""
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: _run("CLARIFYING", 2))

    with pytest.raises(DomainError) as exc:
        service.cancel_run("run-id", expected_version=1, user_id="user-id", user_role="analyst")

    assert exc.value.code == "AGENT_RUN_VERSION_CONFLICT"
    assert exc.value.status_code == 409


def test_cancel_run_persists_terminal_stage_event(monkeypatch: pytest.MonkeyPatch):
    """Removing the terminal event would make a cancelled run unreplayable to SSE clients."""
    events: list[dict] = []
    monkeypatch.setattr(service, "get_cursor", _no_cursor)
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: _run("CLARIFYING", 2))
    monkeypatch.setattr(
        service,
        "update_run_status",
        lambda run_id, expected_version, status, **_: _run(status, expected_version + 1),
    )
    monkeypatch.setattr(
        service,
        "append_event",
        lambda run_id, version, event_type, payload, **_: events.append(
            {"run_id": run_id, "version": version, "event_type": event_type, "payload": payload}
        ),
    )

    result = service.cancel_run("run-id", expected_version=2, user_id="user-id", user_role="analyst")

    assert result["status"] == "CANCELLED"
    assert result["version"] == 3
    assert events == [{"run_id": "run-id", "version": 3, "event_type": "done", "payload": {"status": "CANCELLED"}}]


def test_get_sourcing_risk_run_hides_foreign_run_from_non_admin(monkeypatch: pytest.MonkeyPatch):
    """Replacing owner filtering with a global lookup would disclose another user's requirement."""
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: None)

    with pytest.raises(DomainError) as exc:
        service.get_sourcing_risk_run("run-id", "viewer-id", "viewer")

    assert (exc.value.code, exc.value.status_code) == ("AGENT_RUN_NOT_FOUND", 404)


def test_submit_clarification_resumes_only_clarifying_run(monkeypatch: pytest.MonkeyPatch):
    """Allowing answers on arbitrary states could overwrite an executing run's workflow input."""
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: _run("LOCAL_SEARCHING", 1))

    with pytest.raises(DomainError) as exc:
        service.submit_clarification(
            "run-id", ClarificationRequest(expected_version=1, answers={"region": "华东"}), "user-id", "analyst"
        )

    assert exc.value.code == "AGENT_RUN_INVALID_STATE"


def test_create_run_persists_created_event(monkeypatch: pytest.MonkeyPatch):
    """Removing the first event would prevent a subscriber from reconstructing the lifecycle start."""
    events: list[dict] = []
    monkeypatch.setattr(service, "get_cursor", _no_cursor)
    monkeypatch.setattr(service, "insert_run", lambda **_: _run())
    monkeypatch.setattr(service, "append_event", lambda *args, **_: events.append(args))

    result = service.create_sourcing_risk_run(
        CreateSourcingRiskRunRequest(requirement_text="采购工业摄像头"), "user-id", "analyst"
    )

    assert result["status"] == "CREATED"
    assert events == [(result["id"], 1, "stage", {"status": "CREATED"})]


def _no_cursor():
    class _CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, *_):
            return False

    return _CursorContext()
