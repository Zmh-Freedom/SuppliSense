from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.errors import DomainError
from app.domains.agent_run import service


RUN_ID = "00000000-0000-4000-8000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000011"
CANDIDATE_A = "00000000-0000-4000-8000-00000000000a"
CANDIDATE_B = "00000000-0000-4000-8000-00000000000b"
COMPANY_A = "00000000-0000-4000-8000-00000000000c"


def _run(status: str = "IDENTITY_REVIEW", version: int = 3) -> dict[str, Any]:
    return {
        "id": RUN_ID,
        "run_type": "sourcing_risk_v2",
        "user_id": USER_ID,
        "status": status,
        "version": version,
        "requirement": {"requirement_text": "采购工业摄像头"},
        "created_at": datetime.now(timezone.utc),
    }


def _candidate_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": CANDIDATE_A,
            "candidate_id": CANDIDATE_A,
            "candidate_key": "mutable-name-a",
            "company_id": COMPANY_A,
            "identity_status": "exact",
            "score_eligible": True,
        },
        {
            "id": CANDIDATE_B,
            "candidate_id": CANDIDATE_B,
            "candidate_key": "mutable-name-b",
            "company_id": None,
            "identity_status": "pending_verification",
            "identity_candidates": [],
            "score_eligible": False,
        },
    ]


def _install_fakes(monkeypatch: pytest.MonkeyPatch, candidates: list[dict[str, Any]] | None = None):
    observed: dict[str, Any] = {"upserts": [], "events": [], "updates": [], "intents": []}
    cursor = object()

    class CursorContext:
        def __enter__(self):
            return None, cursor

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(service, "get_cursor", CursorContext)
    monkeypatch.setattr(service, "get_run_for_update", lambda *_: _run())
    monkeypatch.setattr(service, "lock_candidate_review_request_with_cursor", lambda *_: None)
    monkeypatch.setattr(service, "get_candidate_review_event_by_request_with_cursor", lambda *_: None)
    monkeypatch.setattr(service, "list_candidates_with_cursor", lambda *_: candidates or _candidate_rows())
    monkeypatch.setattr(service, "list_sourcing_candidate_dispositions_with_cursor", lambda *_: [])
    monkeypatch.setattr(
        service,
        "upsert_sourcing_candidate_disposition_with_cursor",
        lambda **kwargs: observed["upserts"].append(kwargs) or kwargs,
    )
    monkeypatch.setattr(
        service,
        "update_run_status",
        lambda run_id, expected_version, status, **kwargs: observed["updates"].append(
            {"run_id": run_id, "expected_version": expected_version, "status": status, "cur": kwargs.get("cur")}
        ) or _run(status, expected_version + 1),
    )
    monkeypatch.setattr(
        service,
        "append_event",
        lambda run_id, version, event_type, payload, **kwargs: observed["events"].append(
            {"run_id": run_id, "version": version, "event_type": event_type, "payload": payload, "cur": kwargs.get("cur")}
        ),
    )
    monkeypatch.setattr(
        service,
        "insert_candidate_review_event_with_cursor",
        lambda **kwargs: observed["intents"].append(kwargs) or kwargs,
    )
    return observed, cursor


def test_candidate_batch_persists_pending_defer_and_resume_intent_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
):
    observed, cursor = _install_fakes(monkeypatch)

    result = service.submit_candidate_decisions(
        RUN_ID,
        {
            "expected_version": 3,
            "decisions": [{"candidate_key": CANDIDATE_B, "action": "defer", "reason": "等待资质文件"}],
        },
        USER_ID,
        "purchaser",
        "batch-key-1",
    )

    assert result["run_version"] == 4
    assert result["resume_status"] == "pending"
    assert result["dispositions"] == [{
        "candidate_key": CANDIDATE_B,
        "action": "defer",
        "company_id": None,
        "reason": "等待资质文件",
    }]
    assert observed["updates"][0]["status"] == "IDENTITY_RESOLVING"
    assert observed["upserts"][0]["candidate_id"] == CANDIDATE_B
    assert observed["upserts"][0]["reason"] == "等待资质文件"
    assert observed["events"][0]["cur"] is cursor
    assert observed["intents"][0]["cur"] is cursor
    assert observed["intents"][0]["dispatch_status"] == "pending"


def test_all_excluded_candidates_end_in_needs_review_without_resume_intent(
    monkeypatch: pytest.MonkeyPatch,
):
    observed, _ = _install_fakes(monkeypatch, candidates=[
        {"id": CANDIDATE_B, "candidate_id": CANDIDATE_B, "company_id": None, "identity_status": "pending_verification"}
    ])

    result = service.submit_candidate_decisions(
        RUN_ID,
        {
            "expected_version": 3,
            "decisions": [{"candidate_key": CANDIDATE_B, "action": "exclude", "reason": "不符合规格"}],
        },
        USER_ID,
        "analyst",
        "batch-key-exclude",
    )

    assert result["resume_status"] == "needs_review"
    assert observed["updates"][0]["status"] == "NEEDS_REVIEW"
    assert observed["intents"][0]["dispatch_status"] == "not_required"


def test_candidate_batch_reuses_same_idempotency_response_without_advancing_run(
    monkeypatch: pytest.MonkeyPatch,
):
    observed, _ = _install_fakes(monkeypatch)
    payload = {
        "expected_version": 3,
        "decisions": [{"candidate_key": CANDIDATE_B, "action": "defer", "reason": "等待资料"}],
    }
    first = service.submit_candidate_decisions(RUN_ID, payload, USER_ID, "purchaser", "same-key")
    event = observed["intents"][0]
    monkeypatch.setattr(
        service,
        "get_candidate_review_event_by_request_with_cursor",
        lambda *_: {
            "run_id": RUN_ID,
            "request_hash": event["request_hash"],
            "response": first,
        },
    )

    replay = service.submit_candidate_decisions(RUN_ID, payload, USER_ID, "purchaser", "same-key")

    assert replay == first
    assert len(observed["updates"]) == 1
    assert len(observed["intents"]) == 1


def test_candidate_batch_rejects_reused_idempotency_key_with_different_hash(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fakes(monkeypatch)
    monkeypatch.setattr(
        service,
        "get_candidate_review_event_by_request_with_cursor",
        lambda *_: {"run_id": RUN_ID, "request_hash": "different-hash", "response": {}},
    )

    with pytest.raises(DomainError) as exc:
        service.submit_candidate_decisions(
            RUN_ID,
            {"expected_version": 3, "decisions": []},
            USER_ID,
            "purchaser",
            "reused-key",
        )

    assert exc.value.code == "SOURCING_CANDIDATE_IDEMPOTENCY_CONFLICT"
    assert exc.value.status_code == 409


def test_candidate_batch_requires_a_persisted_candidate_key(monkeypatch: pytest.MonkeyPatch):
    _install_fakes(monkeypatch, candidates=[{"company_id": COMPANY_A, "supplier_name": "mutable name"}])

    with pytest.raises(DomainError) as exc:
        service.submit_candidate_decisions(
            RUN_ID,
            {"expected_version": 3, "decisions": []},
            USER_ID,
            "purchaser",
            "missing-key",
        )

    assert exc.value.code == "SOURCING_CANDIDATE_KEY_UNAVAILABLE"
