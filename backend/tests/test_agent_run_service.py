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
    monkeypatch.setattr(service, "get_raw_payload_lifecycle_statuses", lambda refs: [])
    monkeypatch.setattr(service, "list_raw_payload_compensations", lambda *_: [])

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


def test_get_sourcing_risk_run_exposes_pending_raw_payload_compensation_status(monkeypatch: pytest.MonkeyPatch):
    """Treating a failed raw cleanup as committed would mislead operational audit and recovery."""
    run = _run("INVESTIGATING", 4)
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: run)
    monkeypatch.setattr(
        service,
        "get_run_detail_collections",
        lambda *_: {
            "candidates": [],
            "evidence_by_company_id": {"company-1": [{"raw_payload_ref": "raw-1"}]},
            "evidence_reviews": {},
            "decisions": [],
            "action_proposals": [],
            "approvals": [],
        },
    )
    monkeypatch.setattr(
        service,
        "get_raw_payload_lifecycle_statuses",
        lambda refs: [{"raw_payload_ref": "raw-1", "lifecycle_status": "pending_compensation"}],
    )
    monkeypatch.setattr(service, "list_raw_payload_compensations", lambda *_: [])

    result = service.get_sourcing_risk_run("run-id", "user-id", "analyst")

    assert result["raw_payload_statuses"] == [
        {"raw_payload_ref": "raw-1", "lifecycle_status": "pending_compensation"}
    ]


def test_merge_compensation_statuses_preserves_known_pg_recovery_lifecycle():
    """PG recovery states stay actionable instead of being collapsed into unknown."""
    assert service._merge_compensation_statuses(
        [], [{"raw_payload_ref": "raw-1", "status": "pending_compensation"}]
    ) == [{"raw_payload_ref": "raw-1", "lifecycle_status": "pending_compensation"}]


@pytest.mark.parametrize(
    ("mongo_status", "expected_status"),
    [
        ("pending", "pending"),
        ("pending_compensation", "pending_compensation"),
        ("unknown", "unknown"),
    ],
)
def test_merge_compensation_statuses_never_promotes_pg_compensated_over_unsafe_mongo(
    mongo_status: str, expected_status: str
):
    """A stale PG recovery row must not hide an uncommitted Mongo payload."""
    assert service._merge_compensation_statuses(
        [{"raw_payload_ref": "raw-1", "lifecycle_status": mongo_status}],
        [{"raw_payload_ref": "raw-1", "status": "compensated"}],
    ) == [{"raw_payload_ref": "raw-1", "lifecycle_status": expected_status}]


def test_merge_compensation_statuses_marks_pg_compensated_with_missing_mongo_as_unknown():
    """A missing Mongo observation is not proof that compensation completed."""
    assert service._merge_compensation_statuses(
        [], [{"raw_payload_ref": "raw-1", "status": "compensated"}]
    ) == [{"raw_payload_ref": "raw-1", "lifecycle_status": "unknown"}]


def test_merge_compensation_statuses_allows_only_two_explicitly_safe_lifecycles():
    """Cross-store completion requires an explicit safe lifecycle in both stores."""
    for mongo_status in ("committed", "compensated"):
        for pg_status in ("committed", "compensated"):
            assert service._merge_compensation_statuses(
                [{"raw_payload_ref": "raw-1", "lifecycle_status": mongo_status}],
                [{"raw_payload_ref": "raw-1", "status": pg_status}],
            ) == [{
                "raw_payload_ref": "raw-1",
                "lifecycle_status": "compensated"
                if "compensated" in {mongo_status, pg_status}
                else "committed",
            }]


def test_get_sourcing_risk_run_fails_closed_when_raw_payload_record_is_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    run = _run("SCORING", 4)
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: run)
    monkeypatch.setattr(
        service,
        "get_run_detail_collections",
        lambda *_: {
            "candidates": [{"id": "candidate-1", "score_eligible": True}],
            "evidence_by_company_id": {"company-1": [{"raw_payload_ref": "raw-1"}]},
            "evidence_reviews": {},
            "decisions": [{"candidate_id": "candidate-1", "score_eligible": True}],
            "action_proposals": [],
            "approvals": [],
        },
    )
    monkeypatch.setattr(service, "get_raw_payload_lifecycle_statuses", lambda refs: [
        {"raw_payload_ref": "raw-1", "lifecycle_status": "unknown"}
    ])
    monkeypatch.setattr(service, "list_raw_payload_compensations", lambda *_: [])

    result = service.get_sourcing_risk_run("run-id", "user-id", "analyst")

    assert result["candidates"][0]["score_eligible"] is False
    assert result["decisions"][0]["score_eligible"] is False
    assert result["decisions"][0]["recovery_required"] is True


def test_get_sourcing_risk_run_fails_closed_when_evidence_raw_payload_ref_is_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    run = _run("SCORING", 4)
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: run)
    monkeypatch.setattr(
        service,
        "get_run_detail_collections",
        lambda *_: {
            "candidates": [{"id": "candidate-1", "score_eligible": True}],
            "evidence_by_company_id": {"company-1": [{"evidence_id": "evidence-1", "dimension": "sanctions"}]},
            "evidence_reviews": {},
            "decisions": [{"candidate_id": "candidate-1", "score_eligible": True}],
            "action_proposals": [],
            "approvals": [],
        },
    )
    monkeypatch.setattr(service, "get_raw_payload_lifecycle_statuses", lambda refs: [
        {"raw_payload_ref": refs[0], "lifecycle_status": "unknown"}
    ])
    monkeypatch.setattr(service, "list_raw_payload_compensations", lambda *_: [])

    result = service.get_sourcing_risk_run("run-id", "user-id", "analyst")

    assert result["raw_payload_statuses"] == [
        {"raw_payload_ref": "missing:company-1:evidence-1", "lifecycle_status": "unknown"}
    ]
    assert result["candidates"][0]["score_eligible"] is False
    assert result["decisions"][0]["recovery_required"] is True


def test_get_sourcing_risk_run_fails_closed_for_unknown_recovery_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
):
    run = _run("SCORING", 4)
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: run)
    monkeypatch.setattr(
        service,
        "get_run_detail_collections",
        lambda *_: {
            "candidates": [{"id": "candidate-1", "score_eligible": True}],
            "evidence_by_company_id": {"company-1": [{"raw_payload_ref": "raw-1"}]},
            "evidence_reviews": {},
            "decisions": [{"candidate_id": "candidate-1", "score_eligible": True}],
            "action_proposals": [],
            "approvals": [],
        },
    )
    monkeypatch.setattr(service, "get_raw_payload_lifecycle_statuses", lambda refs: [
        {"raw_payload_ref": "raw-1", "lifecycle_status": "future_state"}
    ])
    monkeypatch.setattr(service, "list_raw_payload_compensations", lambda *_: [])

    result = service.get_sourcing_risk_run("run-id", "user-id", "analyst")

    assert result["raw_payload_statuses"] == [
        {"raw_payload_ref": "raw-1", "lifecycle_status": "unknown"}
    ]
    assert result["candidates"][0]["score_eligible"] is False
    assert result["decisions"][0]["score_eligible"] is False


def test_get_sourcing_risk_run_fails_closed_when_pg_compensated_conflicts_with_mongo_pending(
    monkeypatch: pytest.MonkeyPatch,
):
    """A stale compensated index must not make an uncommitted payload scoreable."""
    run = _run("SCORING", 4)
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: run)
    monkeypatch.setattr(
        service,
        "get_run_detail_collections",
        lambda *_: {
            "candidates": [{"id": "candidate-1", "score_eligible": True}],
            "evidence_by_company_id": {"company-1": [{"raw_payload_ref": "raw-1"}]},
            "evidence_reviews": {},
            "decisions": [{"candidate_id": "candidate-1", "score_eligible": True}],
            "action_proposals": [],
            "approvals": [],
        },
    )
    monkeypatch.setattr(
        service,
        "get_raw_payload_lifecycle_statuses",
        lambda refs: [{"raw_payload_ref": refs[0], "lifecycle_status": "pending"}],
    )
    monkeypatch.setattr(
        service,
        "list_raw_payload_compensations",
        lambda *_: [{"raw_payload_ref": "raw-1", "status": "compensated"}],
    )

    result = service.get_sourcing_risk_run("run-id", "user-id", "analyst")

    assert result["raw_payload_statuses"] == [
        {"raw_payload_ref": "raw-1", "lifecycle_status": "pending"}
    ]
    assert result["candidates"][0]["score_eligible"] is False
    assert result["decisions"][0]["score_eligible"] is False
    assert result["decisions"][0]["recovery_required"] is True


def test_retry_missing_raw_payload_keeps_recovery_unknown(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: _run("SCORING", 4))
    monkeypatch.setattr(
        service,
        "get_run_detail_collections",
        lambda *_: {
            "candidates": [],
            "evidence_by_company_id": {"company-1": [{"raw_payload_ref": "raw-1"}]},
            "evidence_reviews": {},
            "decisions": [],
            "action_proposals": [],
            "approvals": [],
        },
    )
    compensation = [{
        "raw_payload_ref": "raw-1",
        "run_id": _run()["id"],
        "staging_owner": "attempt-1",
        "status": "pending_compensation",
    }]
    monkeypatch.setattr(service, "list_raw_payload_compensations", lambda *_: compensation)
    monkeypatch.setattr(
        service,
        "retry_raw_payload_compensations",
        lambda *_args, **_kwargs: [{"raw_payload_ref": "raw-1", "lifecycle_status": "unknown"}],
    )
    updated: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        service,
        "update_raw_payload_compensation",
        lambda run_id, ref, status, last_error=None: updated.append((ref, status, last_error or "")),
    )

    result = service.retry_sourcing_risk_raw_payload_compensations("run-id", "user-id", "analyst")

    assert result == [{"raw_payload_ref": "raw-1", "lifecycle_status": "unknown"}]
    assert updated == [("raw-1", "unknown", "")]


def test_retry_after_compensation_preserves_unsafe_mongo_state(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: _run("SCORING", 4))
    monkeypatch.setattr(
        service,
        "get_run_detail_collections",
        lambda *_: {
            "candidates": [],
            "evidence_by_company_id": {"company-1": [{"raw_payload_ref": "raw-1"}]},
            "evidence_reviews": {},
            "decisions": [],
            "action_proposals": [],
            "approvals": [],
        },
    )
    compensation = [{
        "raw_payload_ref": "raw-1",
        "run_id": _run()["id"],
        "staging_owner": "attempt-1",
        "status": "compensated",
    }]
    monkeypatch.setattr(service, "list_raw_payload_compensations", lambda *_: compensation)
    monkeypatch.setattr(service, "retry_raw_payload_compensations", lambda *_args, **_kwargs: [
        {"raw_payload_ref": "raw-1", "lifecycle_status": "unknown"}
    ])
    updated: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service,
        "update_raw_payload_compensation",
        lambda run_id, ref, status, last_error=None: updated.append((ref, status)),
    )

    result = service.retry_sourcing_risk_raw_payload_compensations("run-id", "user-id", "analyst")

    assert result == [{"raw_payload_ref": "raw-1", "lifecycle_status": "unknown"}]
    assert updated == [("raw-1", "unknown")]


@pytest.mark.parametrize("mongo_outcome", ["pending_compensation", "unknown"])
def test_retry_does_not_promote_unsafe_mongo_outcome_over_pg_compensated(
    monkeypatch: pytest.MonkeyPatch, mongo_outcome: str
):
    """A current unsafe Mongo observation must remain visible despite stale PG completion."""
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: _run("SCORING", 4))
    monkeypatch.setattr(
        service,
        "get_run_detail_collections",
        lambda *_: {
            "candidates": [],
            "evidence_by_company_id": {"company-1": [{"raw_payload_ref": "raw-1"}]},
            "evidence_reviews": {},
            "decisions": [],
            "action_proposals": [],
            "approvals": [],
        },
    )
    compensation = [{"raw_payload_ref": "raw-1", "status": "compensated"}]
    monkeypatch.setattr(service, "list_raw_payload_compensations", lambda *_: compensation)
    monkeypatch.setattr(
        service,
        "retry_raw_payload_compensations",
        lambda *_args, **_kwargs: [
            {"raw_payload_ref": "raw-1", "lifecycle_status": mongo_outcome}
        ],
    )
    updated: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service,
        "update_raw_payload_compensation",
        lambda _run_id, ref, status, last_error=None: updated.append((ref, status)),
    )

    result = service.retry_sourcing_risk_raw_payload_compensations(
        "run-id", "user-id", "analyst"
    )

    assert result == [{"raw_payload_ref": "raw-1", "lifecycle_status": mongo_outcome}]
    assert updated == [("raw-1", mongo_outcome)]


def test_repeated_retry_after_compensation_keeps_compensated_state(
    monkeypatch: pytest.MonkeyPatch,
):
    """A second ambiguous retry must not downgrade the first successful retry."""
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: _run("SCORING", 4))
    monkeypatch.setattr(
        service,
        "get_run_detail_collections",
        lambda *_: {
            "candidates": [],
            "evidence_by_company_id": {"company-1": [{"raw_payload_ref": "raw-1"}]},
            "evidence_reviews": {},
            "decisions": [],
            "action_proposals": [],
            "approvals": [],
        },
    )
    compensation = [{"raw_payload_ref": "raw-1", "status": "pending_compensation"}]
    monkeypatch.setattr(service, "list_raw_payload_compensations", lambda *_: compensation)
    outcomes = iter(
        [
            [{"raw_payload_ref": "raw-1", "lifecycle_status": "compensated"}],
            [{"raw_payload_ref": "raw-1", "lifecycle_status": "unknown"}],
        ]
    )
    monkeypatch.setattr(service, "retry_raw_payload_compensations", lambda *_args, **_kwargs: next(outcomes))

    def update(_run_id: str, _ref: str, status: str, last_error: str | None = None) -> None:
        if compensation[0]["status"] != "compensated":
            compensation[0]["status"] = status

    monkeypatch.setattr(service, "update_raw_payload_compensation", update)

    first = service.retry_sourcing_risk_raw_payload_compensations("run-id", "user-id", "analyst")
    second = service.retry_sourcing_risk_raw_payload_compensations("run-id", "user-id", "analyst")

    assert first == [{"raw_payload_ref": "raw-1", "lifecycle_status": "compensated"}]
    assert second == [{"raw_payload_ref": "raw-1", "lifecycle_status": "unknown"}]
    assert compensation[0]["status"] == "compensated"


def test_retry_run_raw_payload_compensations_uses_only_authorized_evidence_refs(
    monkeypatch: pytest.MonkeyPatch,
):
    """A recovery command must not let a caller delete raw payloads outside the authorized Run."""
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: _run("INVESTIGATING", 4))
    monkeypatch.setattr(
        service,
        "get_run_detail_collections",
        lambda *_: {
            "candidates": [],
            "evidence_by_company_id": {
                "company-1": [{"raw_payload_ref": "raw-1"}],
                "company-2": [{"raw_payload_ref": "raw-1"}, {"raw_payload_ref": None}],
            },
            "evidence_reviews": {},
            "decisions": [],
            "action_proposals": [],
            "approvals": [],
        },
    )
    monkeypatch.setattr(service, "list_raw_payload_compensations", lambda *_: [])
    observed: list[list[str]] = []
    monkeypatch.setattr(
        service,
        "retry_raw_payload_compensations",
        lambda refs: observed.append(refs) or [{"raw_payload_ref": "raw-1", "lifecycle_status": "compensated"}],
    )

    result = service.retry_sourcing_risk_raw_payload_compensations("run-id", "user-id", "analyst")

    assert observed == [["raw-1"]]
    assert result == [{"raw_payload_ref": "raw-1", "lifecycle_status": "compensated"}]


def test_retry_run_excludes_synthetic_missing_raw_payload_refs(
    monkeypatch: pytest.MonkeyPatch,
):
    """A detail-only missing ref must never become a Mongo deletion target."""
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: _run("INVESTIGATING", 4))
    monkeypatch.setattr(
        service,
        "get_run_detail_collections",
        lambda *_: {
            "candidates": [],
            "evidence_by_company_id": {"company-1": [{"raw_payload_ref": None}]},
            "evidence_reviews": {},
            "decisions": [],
            "action_proposals": [],
            "approvals": [],
        },
    )
    monkeypatch.setattr(service, "list_raw_payload_compensations", lambda *_: [])
    observed: list[list[str]] = []
    monkeypatch.setattr(
        service,
        "retry_raw_payload_compensations",
        lambda refs: observed.append(refs) or [],
    )

    assert service.retry_sourcing_risk_raw_payload_compensations(
        "run-id", "user-id", "analyst"
    ) == []
    assert observed == [[]]


def test_detail_and_retry_discover_compensation_record_after_snapshot_rollback(
    monkeypatch: pytest.MonkeyPatch,
):
    """A rolled-back evidence snapshot must not hide its durable recovery record."""
    run = _run("INVESTIGATING", 4)
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: run)
    monkeypatch.setattr(
        service,
        "get_run_detail_collections",
        lambda *_: {
            "candidates": [], "evidence_by_company_id": {}, "evidence_reviews": {},
            "decisions": [], "action_proposals": [], "approvals": [],
        },
    )
    compensation = [{"raw_payload_ref": "raw-1", "run_id": run["id"], "lifecycle_status": "pending_compensation"}]
    monkeypatch.setattr(service, "get_raw_payload_compensations", lambda *_: compensation)
    monkeypatch.setattr(service, "get_raw_payload_lifecycle_statuses", lambda refs: [])
    monkeypatch.setattr(service, "update_raw_payload_compensation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        service,
        "retry_raw_payload_compensations",
        lambda refs: [{"raw_payload_ref": ref, "lifecycle_status": "compensated"} for ref in refs],
    )

    detail = service.get_sourcing_risk_run("run-id", "user-id", "analyst")
    retry = service.retry_sourcing_risk_raw_payload_compensations("run-id", "user-id", "analyst")

    assert detail["raw_payload_statuses"] == [
        {"raw_payload_ref": "raw-1", "lifecycle_status": "pending_compensation"}
    ]
    assert retry == [{"raw_payload_ref": "raw-1", "lifecycle_status": "compensated"}]


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
    monkeypatch.setattr(service, "_record_compensations", lambda *_args: None)
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
    monkeypatch.setattr(service, "_record_compensations", lambda *_args: None)
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
    monkeypatch.setattr(service, "_record_compensations", lambda *_args: None)
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
            "staging_owner": payloads.documents["raw-1"]["staging_owner"],
        }
    }


class _CompensationPayloads:
    """Stateful Mongo fake used to exercise cross-store compensation outcomes."""

    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}
        self.fail_stage_for: str | None = None
        self.delete_mode = "success"
        self.delete_selectors: list[dict] = []

    def update_one(self, selector: dict, update: dict, *, upsert: bool = False):
        raw_payload_ref = selector["raw_payload_ref"]
        if "$setOnInsert" in update:
            if raw_payload_ref == self.fail_stage_for:
                raise RuntimeError("Mongo staging failed")
            if raw_payload_ref not in self.documents:
                self.documents[raw_payload_ref] = dict(update["$setOnInsert"])
                return type("Result", (), {"upserted_id": raw_payload_ref, "matched_count": 0})()
            return type("Result", (), {"upserted_id": None, "matched_count": 1})()
        document = self.documents.get(raw_payload_ref)
        if document is not None and all(document.get(key) == value for key, value in selector.items()):
            document.update(update["$set"])
            return type("Result", (), {"upserted_id": None, "matched_count": 1})()
        return type("Result", (), {"upserted_id": None, "matched_count": 0})()

    def delete_one(self, selector: dict):
        self.delete_selectors.append(dict(selector))
        if self.delete_mode == "raise":
            raise RuntimeError("Mongo delete failed")
        if self.delete_mode == "zero":
            return type("Result", (), {"deleted_count": 0})()
        raw_payload_ref = selector["raw_payload_ref"]
        document = self.documents.get(raw_payload_ref)
        if document is not None and all(document.get(key) == value for key, value in selector.items()):
            del self.documents[raw_payload_ref]
            return type("Result", (), {"deleted_count": 1})()
        return type("Result", (), {"deleted_count": 0})()

    def find_one(self, selector: dict):
        document = self.documents.get(selector["raw_payload_ref"])
        if document is not None and all(document.get(key) == value for key, value in selector.items()):
            return dict(document)
        return None


def _raw_payload(ref: str) -> dict:
    return {
        "raw_payload_ref": ref,
        "run_id": "run-id",
        "company_id": "company-id",
        "dimension": "sanctions",
        "collected_at": datetime.now(timezone.utc),
        "raw_payload": {"provider": "result"},
    }


def _snapshot_dependencies(monkeypatch: pytest.MonkeyPatch, *, fail: Exception | None = None) -> None:
    class CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(service, "get_cursor", CursorContext)
    monkeypatch.setattr(service, "_record_compensations", lambda *_args: None)
    monkeypatch.setattr(service, "get_orchestration_run_for_update", lambda *_: _run("INVESTIGATING", 4))
    monkeypatch.setattr(service, "persist_run_snapshot", lambda *_args, **_kwargs: {"candidates": []})
    monkeypatch.setattr(service, "update_run_status", lambda *_args, **_kwargs: _run("SCORING", 5))
    monkeypatch.setattr(
        service,
        "append_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(fail) if fail is not None else {"event_id": 1},
    )


def test_orchestration_snapshot_compensates_earlier_payloads_when_mongo_staging_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """A mid-batch stage exception must not strand earlier pending raw payloads."""
    from app.domains.sourcing_risk import evidence_service

    payloads = _CompensationPayloads()
    payloads.fail_stage_for = "raw-2"
    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": payloads})
    _snapshot_dependencies(monkeypatch)

    with pytest.raises(RuntimeError, match="Mongo staging failed"):
        service.persist_orchestration_snapshot(
            "run-id", "SCORING", "investigation", {}, raw_payloads=[_raw_payload("raw-1"), _raw_payload("raw-2")]
        )

    assert payloads.documents == {}
    assert payloads.delete_selectors[0]["raw_payload_ref"] == "raw-1"
    assert payloads.delete_selectors[0]["lifecycle_status"] == "pending_compensation"
    assert payloads.delete_selectors[0]["staging_owner"]


def test_postgres_event_failure_keeps_delete_error_as_pending_compensation(
    monkeypatch: pytest.MonkeyPatch,
):
    """A failed cleanup must retain a stable, non-committed remediation record."""
    from app.domains.sourcing_risk import evidence_service

    payloads = _CompensationPayloads()
    payloads.delete_mode = "raise"
    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": payloads})
    _snapshot_dependencies(monkeypatch, fail=RuntimeError("PostgreSQL event failed"))

    with pytest.raises(RuntimeError, match="PostgreSQL event failed"):
        service.persist_orchestration_snapshot(
            "run-id", "SCORING", "investigation", {}, raw_payloads=[_raw_payload("raw-1")]
        )

    assert payloads.documents["raw-1"]["lifecycle_status"] == "pending_compensation"
    assert payloads.documents["raw-1"]["compensation_reason"] == "postgres_snapshot_failed"
    assert payloads.documents["raw-1"]["raw_payload_ref"] == "raw-1"


def test_postgres_snapshot_failure_keeps_zero_delete_as_pending_compensation(
    monkeypatch: pytest.MonkeyPatch,
):
    """A zero-delete acknowledgement cannot be mistaken for completed compensation."""
    from app.domains.sourcing_risk import evidence_service

    payloads = _CompensationPayloads()
    payloads.delete_mode = "zero"
    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": payloads})
    _snapshot_dependencies(monkeypatch)
    monkeypatch.setattr(service, "persist_run_snapshot", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("PostgreSQL snapshot failed")))

    with pytest.raises(RuntimeError, match="PostgreSQL snapshot failed"):
        service.persist_orchestration_snapshot(
            "run-id", "SCORING", "investigation", {}, raw_payloads=[_raw_payload("raw-1")]
        )

    assert payloads.documents["raw-1"]["lifecycle_status"] == "pending_compensation"
    assert payloads.documents["raw-1"]["compensation_reason"] == "postgres_snapshot_failed"


def test_retry_raw_payload_compensation_deletes_pending_record_idempotently(monkeypatch: pytest.MonkeyPatch):
    """A recovery retry must remove one pending ref without creating another raw document."""
    from app.domains.sourcing_risk import evidence_service

    payloads = _CompensationPayloads()
    payloads.documents["raw-1"] = {**_raw_payload("raw-1"), "lifecycle_status": "pending_compensation"}
    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": payloads})

    result = evidence_service.retry_raw_payload_compensations(["raw-1", "raw-1"])

    assert result == [{"raw_payload_ref": "raw-1", "lifecycle_status": "compensated"}]
    assert payloads.documents == {}
    assert evidence_service.retry_raw_payload_compensations(["raw-1"]) == [
        {"raw_payload_ref": "raw-1", "lifecycle_status": "unknown"}
    ]


def test_staging_does_not_reactivate_a_committed_raw_payload(monkeypatch: pytest.MonkeyPatch):
    """A retry must never downgrade an already-committed stable payload into compensable state."""
    from app.domains.sourcing_risk import evidence_service

    payloads = _CompensationPayloads()
    payloads.documents["raw-1"] = {**_raw_payload("raw-1"), "lifecycle_status": "committed"}
    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": payloads})

    assert evidence_service.stage_raw_payloads([_raw_payload("raw-1")]) == []
    assert payloads.documents["raw-1"]["lifecycle_status"] == "committed"


def test_retry_raw_payload_compensation_retains_failed_delete_for_another_retry(monkeypatch: pytest.MonkeyPatch):
    """Repeated recovery failure must preserve one stable pending-compensation record, never commit it."""
    from app.domains.sourcing_risk import evidence_service

    payloads = _CompensationPayloads()
    payloads.delete_mode = "raise"
    payloads.documents["raw-1"] = {**_raw_payload("raw-1"), "lifecycle_status": "pending_compensation"}
    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": payloads})

    result = evidence_service.retry_raw_payload_compensations(["raw-1"])

    assert result == [{"raw_payload_ref": "raw-1", "lifecycle_status": "pending_compensation"}]
    assert payloads.documents["raw-1"]["lifecycle_status"] == "pending_compensation"
    assert payloads.documents["raw-1"]["raw_payload_ref"] == "raw-1"


def test_compensation_index_failure_is_explicit_and_fail_closed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(service, "list_raw_payload_compensations", lambda *_: (_ for _ in ()).throw(RuntimeError("table unavailable")))

    with pytest.raises(DomainError) as exc:
        service.get_raw_payload_compensations("run-id")

    assert exc.value.code == "AGENT_RUN_RECOVERY_UNAVAILABLE"
    assert exc.value.status_code == 503


def test_postgres_commit_failure_records_durable_recovery(monkeypatch: pytest.MonkeyPatch):
    payload = _raw_payload("raw-1")
    recorded: list[tuple[list[dict], str]] = []
    class CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(service, "stage_raw_payloads", lambda *_args, **_kwargs: [{**payload, "staging_owner": "attempt-1", "lifecycle_status": "pending"}])
    monkeypatch.setattr(service, "commit_raw_payloads", lambda *_: (_ for _ in ()).throw(RuntimeError("mongo commit failed")))
    monkeypatch.setattr(service, "_record_compensations", lambda payloads, reason: recorded.append((payloads, reason)))
    monkeypatch.setattr(service, "compensate_raw_payloads", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "get_cursor", CursorContext)
    monkeypatch.setattr(service, "get_orchestration_run_for_update", lambda *_: _run("INVESTIGATING", 4))
    monkeypatch.setattr(service, "persist_run_snapshot", lambda *_args, **_kwargs: {"candidates": []})
    monkeypatch.setattr(service, "update_run_status", lambda *_args, **_kwargs: _run("SCORING", 5))
    monkeypatch.setattr(service, "append_event", lambda *_args, **_kwargs: {"event_id": 1})

    with pytest.raises(RuntimeError, match="mongo commit failed"):
        service.persist_orchestration_snapshot("run-id", "SCORING", "investigation", {}, raw_payloads=[payload])

    assert recorded[0][0][0]["raw_payload_ref"] == "raw-1"
    assert recorded[0][0][0]["staging_owner"] == "attempt-1"
    assert recorded[0][1] == "mongo_commit_failed"


def test_each_snapshot_attempt_uses_a_unique_staging_owner(monkeypatch: pytest.MonkeyPatch):
    owners: list[str] = []
    monkeypatch.setattr(service, "stage_raw_payloads", lambda _payloads, *, staging_owner: owners.append(staging_owner) or [])
    monkeypatch.setattr(service, "get_cursor", _no_cursor)
    monkeypatch.setattr(service, "get_orchestration_run_for_update", lambda *_: _run("INVESTIGATING", 4))
    monkeypatch.setattr(service, "persist_run_snapshot", lambda *_args, **_kwargs: {"candidates": []})
    monkeypatch.setattr(service, "update_run_status", lambda *_args, **_kwargs: _run("SCORING", 5))
    monkeypatch.setattr(service, "append_event", lambda *_args, **_kwargs: {"event_id": 1})
    payload = _raw_payload("raw-1")

    run_id = _run()["id"]
    service.persist_orchestration_snapshot(run_id, "SCORING", "investigation", {}, raw_payloads=[payload])
    service.persist_orchestration_snapshot(run_id, "SCORING", "investigation", {}, raw_payloads=[payload])

    assert len(owners) == 2
    assert owners[0] != owners[1]


def test_ambiguous_stage_confirmation_failure_is_compensable(monkeypatch: pytest.MonkeyPatch):
    class Collection:
        def update_one(self, *_args, **_kwargs):
            raise RuntimeError("ack lost")

        def find_one(self, *_args, **_kwargs):
            raise RuntimeError("confirmation unavailable")

    from app.domains.sourcing_risk import evidence_service
    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": Collection()})

    with pytest.raises(evidence_service.RawPayloadStagingError) as exc:
        evidence_service.stage_raw_payloads([_raw_payload("raw-1")], staging_owner="attempt-1")

    assert exc.value.compensation_payloads[0]["raw_payload_ref"] == "raw-1"
    assert exc.value.compensation_payloads[0]["staging_owner"] == "attempt-1"


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
