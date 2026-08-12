"""Behavior tests for sourcing-risk evidence normalization and safety states."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.domains.sourcing_risk import evidence_service


def _policy_requiring_sanctions() -> dict:
    return {
        "required_evidence": ["sanctions"],
        "freshness_days": {"sanctions": 30},
    }


def _sanctions(claim_code: str, **overrides: object) -> dict:
    evidence = {
        "dimension": "sanctions",
        "claim_code": claim_code,
        "freshness_status": "fresh",
        "conflict_status": "none",
    }
    evidence.update(overrides)
    return evidence


def test_missing_sanctions_evidence_requires_review():
    """Treating absent mandatory sanctions data as clear would permit blind approval."""
    outcome = evidence_service.validate_evidence_set([], _policy_requiring_sanctions())

    assert outcome["status"] == "needs_review"
    assert outcome["reason_codes"] == ["SANCTIONS_DATA_UNAVAILABLE"]
    assert outcome["evidence_present"] is False
    assert outcome["claim_status"] == "missing"
    assert outcome["score_eligible"] is False


def test_conflicting_key_evidence_requires_review():
    """Selecting one contradictory sanctions result would conceal a key conflict."""
    outcome = evidence_service.validate_evidence_set(
        [_sanctions("clear"), _sanctions("hit")], _policy_requiring_sanctions()
    )

    assert outcome["status"] == "needs_review"
    assert "KEY_EVIDENCE_CONFLICT" in outcome["reason_codes"]
    assert outcome["claim_status"] == "conflicting"


def test_stale_required_evidence_is_incomplete_not_clear():
    """A stale clean result must not be promoted to a current low-risk signal."""
    outcome = evidence_service.validate_evidence_set(
        [_sanctions("clear", freshness_status="stale")], _policy_requiring_sanctions()
    )

    assert outcome == {
        "status": "incomplete",
        "reason_codes": ["SANCTIONS_DATA_STALE"],
        "evidence_present": True,
        "claim_status": "stale",
        "score_eligible": False,
    }


def test_normalize_clear_claim_defers_all_persistence_to_run_snapshot(monkeypatch):
    """Writing Mongo here would leave a raw-payload orphan when the snapshot event rolls back."""
    observed_at = datetime.now(timezone.utc) - timedelta(days=2)
    monkeypatch.setattr(
        evidence_service,
        "get_db",
        lambda: (_ for _ in ()).throw(AssertionError("normalization must not write MongoDB")),
    )
    run_id = str(uuid4())
    company_id = str(uuid4())
    record = evidence_service.normalize_evidence(
        run_id,
        company_id,
        "sanctions",
        {
            "claim_code": "clear",
            "source_type": "sanctions_provider",
            "source_reference": "record-42",
            "observed_at": observed_at.isoformat(),
            "freshness_days": 1,
            "raw_payload": {"unbounded": "provider response"},
        },
        policy=_policy_requiring_sanctions(),
    )

    assert record.claim_code == "clear"
    assert record.freshness_status == "fresh"
    assert str(record.company_id) == company_id
    assert record.raw_payload_ref
    assert "raw_payload" not in record.model_dump()


def test_normalize_rejects_non_uuid_run_or_company_id():
    """Accepting names or arbitrary strings would break the canonical identity boundary."""
    with pytest.raises(ValueError, match="run_id must be a UUID"):
        evidence_service.normalize_evidence(
            "run-id", str(uuid4()), "sanctions", {}, policy=_policy_requiring_sanctions()
        )

    with pytest.raises(ValueError, match="company_id must be a UUID"):
        evidence_service.normalize_evidence(
            str(uuid4()), "company-id", "sanctions", {}, policy=_policy_requiring_sanctions()
        )


def test_normalize_uses_frozen_policy_window_not_provider_window(monkeypatch):
    """A provider must not loosen the freshness window used for a sourcing decision."""
    monkeypatch.setattr(
        evidence_service, "get_db", lambda: {"agent_evidence_payloads": type("C", (), {"insert_one": lambda *_: None})()}
    )
    now = datetime.now(timezone.utc)

    record = evidence_service.normalize_evidence(
        str(uuid4()),
        str(uuid4()),
        "sanctions",
        {
            "claim_code": "clear",
            "observed_at": (now - timedelta(days=31)).isoformat(),
            "collected_at": now.isoformat(),
            "freshness_days": 365,
        },
        policy=_policy_requiring_sanctions(),
    )

    assert record.freshness_status == "stale"


def test_unavailable_and_hit_sanctions_are_not_score_eligible():
    """Both a failed lookup and a sanctions match must close the scoring path."""
    unavailable = evidence_service.validate_evidence_set(
        [_sanctions("unavailable")], _policy_requiring_sanctions()
    )
    hit = evidence_service.validate_evidence_set([_sanctions("hit")], _policy_requiring_sanctions())

    assert unavailable["status"] == hit["status"] == "needs_review"
    assert unavailable["score_eligible"] is hit["score_eligible"] is False
    assert unavailable["claim_status"] == "unavailable"
    assert hit["claim_status"] == "hit"


def test_unknown_conflicting_and_resolved_evidence_have_distinct_safety_states():
    """Collapsing lifecycle states would make review and scoring decisions ambiguous."""
    unknown = evidence_service.validate_evidence_set(
        [_sanctions("clear", freshness_status="unknown")], _policy_requiring_sanctions()
    )
    conflicting = evidence_service.validate_evidence_set(
        [_sanctions("clear", conflict_status="conflicting")], _policy_requiring_sanctions()
    )
    resolved = evidence_service.validate_evidence_set(
        [_sanctions("clear", conflict_status="resolved")], _policy_requiring_sanctions()
    )

    assert unknown["claim_status"] == "unknown"
    assert conflicting["claim_status"] == "conflicting"
    assert resolved == {
        "status": "clear",
        "reason_codes": [],
        "evidence_present": True,
        "claim_status": "no_risk",
        "score_eligible": True,
    }


def test_normalize_unknown_conflict_status_is_explicitly_unknown(monkeypatch):
    """Silently converting an unrecognized provider conflict state to none hides uncertainty."""
    monkeypatch.setattr(
        evidence_service, "get_db", lambda: {"agent_evidence_payloads": type("C", (), {"insert_one": lambda *_: None})()}
    )

    record = evidence_service.normalize_evidence(
        str(uuid4()), str(uuid4()), "sanctions", {"conflict_status": "provider_pending"}, policy=_policy_requiring_sanctions()
    )

    assert record.conflict_status == "unknown"


def test_future_observed_at_is_invalid_not_fresh(monkeypatch):
    """A provider timestamp after collection cannot prove present evidence freshness."""
    monkeypatch.setattr(
        evidence_service, "get_db", lambda: {"agent_evidence_payloads": type("C", (), {"insert_one": lambda *_: None})()}
    )
    collected_at = datetime.now(timezone.utc)

    record = evidence_service.normalize_evidence(
        str(uuid4()),
        str(uuid4()),
        "sanctions",
        {"observed_at": (collected_at + timedelta(minutes=1)).isoformat(), "collected_at": collected_at.isoformat()},
        policy=_policy_requiring_sanctions(),
    )

    assert record.freshness_status == "stale"


def test_ambiguous_stage_write_recovers_inserted_document_for_compensation(monkeypatch):
    """A write timeout after Mongo inserted the row must retain this attempt's ownership."""
    class Result:
        upserted_id = None
        matched_count = 1

    class Collection:
        def __init__(self):
            self.document = None

        def update_one(self, selector, update, *, upsert=False):
            if self.document is None:
                self.document = dict(update["$setOnInsert"])
                raise RuntimeError("write acknowledgement lost")
            return Result()

        def find_one(self, selector):
            return dict(self.document) if self.document and self.document["raw_payload_ref"] == selector["raw_payload_ref"] else None

    collection = Collection()
    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": collection})

    payload = {"raw_payload_ref": "raw-1", "run_id": "run-1", "company_id": "company-1"}
    staged = evidence_service.stage_raw_payloads([payload], staging_owner="owner-1")

    assert staged == [{**payload, "lifecycle_status": "pending", "staging_owner": "owner-1"}]


def test_staging_owner_cannot_commit_another_run_payload(monkeypatch):
    """A replay from another run must not change or delete a pending owner's lifecycle."""
    class Result:
        matched_count = 0

    class Collection:
        def __init__(self):
            self.document = {"raw_payload_ref": "raw-1", "run_id": "run-a", "company_id": "company-a", "lifecycle_status": "pending", "staging_owner": "owner-a"}

        def update_one(self, selector, update, *, upsert=False):
            assert selector["run_id"] == "run-b"
            return Result()

        def find_one(self, selector):
            return dict(self.document)

    collection = Collection()
    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": collection})

    with pytest.raises(RuntimeError, match="ownership"):
        evidence_service.commit_raw_payloads(
            [{"raw_payload_ref": "raw-1", "run_id": "run-b", "company_id": "company-b", "staging_owner": "owner-b"}]
        )


def test_retry_missing_raw_payload_is_unknown_not_already_compensated(monkeypatch):
    class Collection:
        def find_one(self, selector):
            return None

    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": Collection()})

    assert evidence_service.retry_raw_payload_compensations(["raw-missing"]) == [
        {"raw_payload_ref": "raw-missing", "lifecycle_status": "unknown"}
    ]


def test_retry_raw_payload_query_failure_is_unknown(monkeypatch):
    class Collection:
        def find_one(self, selector):
            raise RuntimeError("mongo unavailable")

    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": Collection()})

    assert evidence_service.retry_raw_payload_compensations(["raw-unknown"]) == [
        {"raw_payload_ref": "raw-unknown", "lifecycle_status": "unknown"}
    ]


def test_retry_raw_payload_non_pending_record_is_unknown(monkeypatch):
    class Collection:
        def find_one(self, selector):
            return {"raw_payload_ref": "raw-committed", "lifecycle_status": "committed"}

    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": Collection()})

    assert evidence_service.retry_raw_payload_compensations(["raw-committed"]) == [
        {"raw_payload_ref": "raw-committed", "lifecycle_status": "unknown"}
    ]


def test_lifecycle_status_for_missing_mongo_document_is_unknown(monkeypatch):
    class Collection:
        def find_one(self, selector):
            return None

    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": Collection()})

    assert evidence_service.get_raw_payload_lifecycle_statuses(["raw-missing"]) == [
        {"raw_payload_ref": "raw-missing", "lifecycle_status": "unknown"}
    ]


def test_lifecycle_status_for_unknown_mongo_lifecycle_is_unknown(monkeypatch):
    class Collection:
        def find_one(self, selector):
            return {"raw_payload_ref": "raw-corrupt", "lifecycle_status": "future_state"}

    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": Collection()})

    assert evidence_service.get_raw_payload_lifecycle_statuses(["raw-corrupt"]) == [
        {"raw_payload_ref": "raw-corrupt", "lifecycle_status": "unknown"}
    ]
