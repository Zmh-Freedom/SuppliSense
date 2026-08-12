"""Lifecycle contracts for durable sourcing-risk agent runs."""

from datetime import datetime, timezone

import pytest

from app.core.errors import DomainError
from app.domains.agent_run import service
from app.domains.agent_run.schemas import ClarificationRequest, CreateSourcingRiskRunRequest, IdentityResolutionRequest


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


def test_get_sourcing_risk_run_assembles_authorized_workbench_detail(monkeypatch: pytest.MonkeyPatch):
    """Returning only the run row leaves the V2 workbench without persisted results."""
    run = _run("ACTION_PENDING", 4)
    collections = {
        "candidates": [{"id": "candidate-1", "company_id": "company-1", "supplier_name": "示例供应商"}],
        "evidence_by_company_id": {"company-1": [{"evidence_id": "evidence-1", "dimension": "sanctions"}]},
        "evidence_reviews": {"company-1": {"status": "clear"}},
        "decisions": [{"candidate_id": "candidate-1", "group": "recommended", "final_score": 91.0}],
        "action_proposals": [{"id": "approval-1", "action_type": "add_watchlist", "status": "pending"}],
        "approvals": [{"proposal_id": "approval-1", "decision": "approved", "comment": "复核通过"}],
    }
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: run)
    monkeypatch.setattr(service, "get_run_detail_collections", lambda run_id: collections)

    result = service.get_sourcing_risk_run("run-id", "user-id", "analyst")

    assert result["run_id"] == run["id"]
    assert result["id"] == run["id"]
    assert result["requirement"]["requirement_text"] == run["requirement"]["requirement_text"]
    assert result["candidates"] == collections["candidates"]
    assert result["evidence_by_company_id"] == collections["evidence_by_company_id"]
    assert result["decisions"] == collections["decisions"]
    assert result["action_proposals"] == collections["action_proposals"]
    assert result["approvals"] == collections["approvals"]
    assert result["proposals"] == collections["action_proposals"]


def test_stream_events_embeds_renderable_durable_detail(monkeypatch: pytest.MonkeyPatch):
    """A count-only event cannot update candidates, evidence, decisions, or approvals after replay."""
    run = {
        **_run("EVIDENCE_REVIEW", 6),
        "candidates": [{"id": "candidate-1", "company_id": "company-1"}],
        "evidence_by_company_id": {"company-1": [{"evidence_id": "evidence-1"}]},
        "evidence_reviews": {"company-1": {"status": "needs_review"}},
        "decisions": [{"candidate_id": "candidate-1", "group": "needs_review"}],
        "action_proposals": [{"id": "approval-1", "status": "pending"}],
        "approvals": [],
    }
    monkeypatch.setattr(service, "get_sourcing_risk_run", lambda *_: run)
    monkeypatch.setattr(
        service,
        "list_events_after",
        lambda *_: [{"event_id": 7, "version": 6, "event_type": "evidence_validated", "payload": {"stage": "evidence_review", "status": "EVIDENCE_REVIEW"}}],
    )

    event = next(service.stream_events("run-id", 6, "user-id", "analyst"))

    assert event == {
        "event_id": 7,
        "event_type": "evidence_validated",
        "data": {
            "stage": "evidence_review",
            "status": "EVIDENCE_REVIEW",
            "version": 6,
            "candidates": run["candidates"],
            "evidence_by_company_id": run["evidence_by_company_id"],
            "evidence_reviews": run["evidence_reviews"],
            "decisions": run["decisions"],
            "action_proposals": run["action_proposals"],
            "approvals": [],
            "run": run,
        },
    }


def test_submit_clarification_resumes_only_clarifying_run(monkeypatch: pytest.MonkeyPatch):
    """Allowing answers on arbitrary states could overwrite an executing run's workflow input."""
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: _run("LOCAL_SEARCHING", 1))

    with pytest.raises(DomainError) as exc:
        service.submit_clarification(
            "run-id", ClarificationRequest(expected_version=1, answers={"region": "华东"}), "user-id", "analyst"
        )

    assert exc.value.code == "AGENT_RUN_INVALID_STATE"


def test_submit_clarification_merges_answers_and_resets_clarifying_run(monkeypatch: pytest.MonkeyPatch):
    """Keeping answers only in an event would make the resumed graph parse stale input again."""
    events: list[dict] = []
    updated_requirement = {"requirement_text": "采购工业摄像头", "specification": "IP67"}
    monkeypatch.setattr(service, "get_cursor", _no_cursor)
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: _run("CLARIFYING", 2))
    monkeypatch.setattr(
        service,
        "update_run_requirement",
        lambda run_id, expected_version, requirement, status, **_: {
            **_run(status, expected_version + 1),
            "requirement": requirement,
        },
    )
    monkeypatch.setattr(
        service,
        "append_event",
        lambda run_id, version, event_type, payload, **_: events.append(
            {"run_id": run_id, "version": version, "event_type": event_type, "payload": payload}
        ),
    )

    result = service.submit_clarification(
        "run-id",
        ClarificationRequest(expected_version=2, answers={"specification": "IP67"}),
        "user-id",
        "analyst",
    )

    assert result["status"] == "CREATED"
    assert result["version"] == 3
    assert result["requirement"] == updated_requirement
    assert events == [{
        "run_id": "run-id",
        "version": 3,
        "event_type": "clarification",
        "payload": {"answers": {"specification": "IP67"}, "status": "CREATED"},
    }]


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


def test_identity_resolution_versions_a_durable_resume_event(monkeypatch: pytest.MonkeyPatch):
    """A stale reviewer must not overwrite a newer durable identity resolution."""
    events: list[dict] = []
    monkeypatch.setattr(service, "get_cursor", _no_cursor)
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: _run("IDENTITY_REVIEW", 2))
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

    result = service.submit_identity_resolution(
        "run-id",
        IdentityResolutionRequest(expected_version=2, resolutions={"candidate-a": "company-a"}),
        "user-id",
        "analyst",
    )

    assert result["version"] == 3
    assert events == [{
        "run_id": "run-id",
        "version": 3,
        "event_type": "identity_resolution",
        "payload": {"identity_resolutions": {"candidate-a": "company-a"}, "status": "IDENTITY_REVIEW"},
    }]


def test_orchestration_state_transition_writes_typed_event_with_new_version(monkeypatch: pytest.MonkeyPatch):
    """Keeping graph status in the checkpoint alone would strand SSE before its terminal event."""
    events: list[dict] = []
    monkeypatch.setattr(service, "get_cursor", _no_cursor)
    monkeypatch.setattr(service, "get_run", lambda *_: _run("SCORING", 7))
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
        ) or {"event_id": 12},
    )

    event_id = service.record_orchestration_state(
        "run-id", "READY_FOR_REVIEW", "ready_for_review", {"provider_failures": []}
    )

    assert event_id == 12
    assert events == [{
        "run_id": "run-id",
        "version": 8,
        "event_type": "ready_for_review",
        "payload": {"provider_failures": [], "status": "READY_FOR_REVIEW"},
    }]


def test_orchestration_state_rolls_back_when_event_write_fails(monkeypatch: pytest.MonkeyPatch):
    """Committing a status without its event would split checkpoint/SSE recovery histories."""
    observed: list[type[BaseException] | None] = []

    class CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, exc_type, *_):
            observed.append(exc_type)
            return False

    monkeypatch.setattr(service, "get_cursor", CursorContext)
    monkeypatch.setattr(service, "get_run", lambda *_: _run("SCORING", 7))
    monkeypatch.setattr(
        service,
        "update_run_status",
        lambda run_id, expected_version, status, **_: _run(status, expected_version + 1),
    )
    monkeypatch.setattr(service, "append_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("event insert failed")))

    with pytest.raises(RuntimeError, match="event insert failed"):
        service.record_orchestration_state("run-id", "READY_FOR_REVIEW", "ready_for_review", {})

    assert observed == [RuntimeError]


def test_orchestration_snapshot_rolls_back_collections_when_event_write_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """A failed event insert must not commit candidates, reviews, or decisions without replay state."""
    observed: list[type[BaseException] | None] = []

    class CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, exc_type, *_):
            observed.append(exc_type)
            return False

    monkeypatch.setattr(service, "get_cursor", CursorContext)
    monkeypatch.setattr(service, "get_orchestration_run_for_update", lambda *_: _run("INVESTIGATING", 4))
    snapshots: list[dict] = []
    monkeypatch.setattr(
        service,
        "persist_run_snapshot",
        lambda *_args, **kwargs: snapshots.append(kwargs) or {"candidates": [{"candidate_id": "candidate-1"}]},
    )
    monkeypatch.setattr(service, "update_run_status", lambda *_args, **_kwargs: _run("SCORING", 5))
    monkeypatch.setattr(service, "append_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("event insert failed")))

    with pytest.raises(RuntimeError, match="event insert failed"):
        service.persist_orchestration_snapshot(
            "run-id",
            "SCORING",
            "decision",
            {"count": 1},
            candidates=[{"candidate_key": "local:company-1"}],
            evidence_by_company_id={"company-1": [{"dimension": "sanctions"}]},
            decisions=[{"candidate_id": "candidate-1", "group": "recommended"}],
        )

    assert observed == [RuntimeError]
    assert snapshots[0]["evidence_by_company_id"] == {"company-1": [{"dimension": "sanctions"}]}


def test_orchestration_snapshot_removes_new_raw_payload_when_event_write_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """A PostgreSQL event failure must compensate the Mongo payload created for that snapshot."""
    from app.domains.sourcing_risk import evidence_service

    class WriteResult:
        def __init__(self, upserted_id: str | None = None) -> None:
            self.upserted_id = upserted_id

    class DeleteResult:
        def __init__(self, deleted_count: int) -> None:
            self.deleted_count = deleted_count

    class RawPayloads:
        def __init__(self) -> None:
            self.documents: dict[str, dict] = {}

        def update_one(self, selector: dict, update: dict, *, upsert: bool = False) -> WriteResult:
            raw_payload_ref = selector["raw_payload_ref"]
            if raw_payload_ref in self.documents:
                return WriteResult()
            assert upsert is True
            self.documents[raw_payload_ref] = dict(update["$setOnInsert"])
            return WriteResult(raw_payload_ref)

        def delete_one(self, selector: dict) -> DeleteResult:
            raw_payload_ref = selector["raw_payload_ref"]
            if raw_payload_ref not in self.documents:
                return DeleteResult(0)
            del self.documents[raw_payload_ref]
            return DeleteResult(1)

    class CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, *_):
            return False

    payloads = RawPayloads()
    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": payloads})
    monkeypatch.setattr(service, "get_cursor", CursorContext)
    monkeypatch.setattr(service, "get_orchestration_run_for_update", lambda *_: _run("INVESTIGATING", 4))
    monkeypatch.setattr(service, "persist_run_snapshot", lambda *_args, **_kwargs: {"candidates": []})
    monkeypatch.setattr(service, "update_run_status", lambda *_args, **_kwargs: _run("SCORING", 5))
    monkeypatch.setattr(service, "append_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("event insert failed")))

    with pytest.raises(RuntimeError, match="event insert failed"):
        service.persist_orchestration_snapshot(
            "run-id",
            "SCORING",
            "investigation",
            {},
            raw_payloads=[{
                "raw_payload_ref": "raw-1",
                "run_id": "run-id",
                "company_id": "company-id",
                "dimension": "sanctions",
                "collected_at": datetime.now(timezone.utc),
                "raw_payload": {"provider": "result"},
            }],
        )

    assert payloads.documents == {}


def test_orchestration_snapshot_retries_raw_payload_without_duplicate(
    monkeypatch: pytest.MonkeyPatch,
):
    """A retry after a successful snapshot must reuse its stable Mongo raw payload reference."""
    from app.domains.sourcing_risk import evidence_service

    class WriteResult:
        def __init__(self, upserted_id: str | None = None) -> None:
            self.upserted_id = upserted_id

    class RawPayloads:
        def __init__(self) -> None:
            self.documents: dict[str, dict] = {}

        def update_one(self, selector: dict, update: dict, *, upsert: bool = False) -> WriteResult:
            raw_payload_ref = selector["raw_payload_ref"]
            if "$setOnInsert" in update:
                if raw_payload_ref not in self.documents:
                    assert upsert is True
                    self.documents[raw_payload_ref] = dict(update["$setOnInsert"])
                    return WriteResult(raw_payload_ref)
                return WriteResult()
            self.documents[raw_payload_ref].update(update["$set"])
            return WriteResult()

    class CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, *_):
            return False

    raw_payload = {
        "raw_payload_ref": "raw-1",
        "run_id": "run-id",
        "company_id": "company-id",
        "dimension": "sanctions",
        "collected_at": datetime.now(timezone.utc),
        "raw_payload": {"provider": "result"},
    }
    payloads = RawPayloads()
    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": payloads})
    monkeypatch.setattr(service, "get_cursor", CursorContext)
    monkeypatch.setattr(service, "get_orchestration_run_for_update", lambda *_: _run("INVESTIGATING", 4))
    monkeypatch.setattr(service, "persist_run_snapshot", lambda *_args, **_kwargs: {"candidates": []})
    monkeypatch.setattr(service, "update_run_status", lambda *_args, **_kwargs: _run("SCORING", 5))
    monkeypatch.setattr(service, "append_event", lambda *_args, **_kwargs: {"event_id": 1})

    service.persist_orchestration_snapshot("run-id", "SCORING", "investigation", {}, raw_payloads=[raw_payload])
    service.persist_orchestration_snapshot("run-id", "SCORING", "investigation", {}, raw_payloads=[raw_payload])

    assert payloads.documents == {
        "raw-1": {
            **raw_payload,
            "lifecycle_status": "committed",
        }
    }


def test_stream_events_stops_after_replaying_a_durable_terminal_stage(monkeypatch: pytest.MonkeyPatch):
    """Continuing after a final stage would keep completed SSE subscriptions open forever."""
    run = _run("PARTIAL", 8)
    monkeypatch.setattr(service, "get_sourcing_risk_run", lambda *_: run)
    monkeypatch.setattr(
        service,
        "list_events_after",
        lambda *_: [{
            "event_id": 12,
            "event_type": "ready_for_review",
            "payload": {"status": "PARTIAL", "provider_failures": ["financial"]},
        }],
    )

    events = list(service.stream_events("run-id", 11, "user-id", "analyst"))

    assert events[0]["event_id"] == 12
    assert events[0]["event_type"] == "ready_for_review"
    assert events[0]["data"] == {
        "status": "PARTIAL",
        "provider_failures": ["financial"],
        "version": 8,
        "candidates": [],
        "evidence_by_company_id": {},
        "evidence_reviews": {},
        "decisions": [],
        "action_proposals": [],
        "approvals": [],
        "run": run,
    }


def _no_cursor():
    class _CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, *_):
            return False

    return _CursorContext()
