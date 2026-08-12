"""Behavior tests for sourcing-risk evidence normalization and safety states."""

from datetime import datetime, timedelta, timezone

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


def test_conflicting_key_evidence_requires_review():
    """Selecting one contradictory sanctions result would conceal a key conflict."""
    outcome = evidence_service.validate_evidence_set(
        [_sanctions("clear"), _sanctions("hit")], _policy_requiring_sanctions()
    )

    assert outcome["status"] == "needs_review"
    assert "KEY_EVIDENCE_CONFLICT" in outcome["reason_codes"]


def test_stale_required_evidence_is_incomplete_not_clear():
    """A stale clean result must not be promoted to a current low-risk signal."""
    outcome = evidence_service.validate_evidence_set(
        [_sanctions("clear", freshness_status="stale")], _policy_requiring_sanctions()
    )

    assert outcome == {"status": "incomplete", "reason_codes": ["SANCTIONS_DATA_STALE"]}


def test_normalize_clear_claim_keeps_raw_payload_only_by_reference(monkeypatch):
    """Leaking provider payload into the record would break the structured evidence boundary."""
    observed_at = datetime.now(timezone.utc) - timedelta(days=2)
    persisted: dict[str, object] = {}

    class FakeCollection:
        def insert_one(self, document: dict) -> None:
            persisted["raw"] = document

    monkeypatch.setattr(evidence_service, "get_db", lambda: {"agent_evidence_payloads": FakeCollection()})
    monkeypatch.setattr(
        evidence_service.agent_run_repo,
        "insert_evidence",
        lambda **kwargs: persisted.setdefault("structured", kwargs) or {"id": "evidence-id"},
    )

    record = evidence_service.normalize_evidence(
        "run-id",
        "company-id",
        "sanctions",
        {
            "claim_code": "clear",
            "source_type": "sanctions_provider",
            "source_reference": "record-42",
            "observed_at": observed_at.isoformat(),
            "freshness_days": 30,
            "raw_payload": {"unbounded": "provider response"},
        },
    )

    assert record.claim_code == "clear"
    assert record.freshness_status == "fresh"
    assert record.raw_payload_ref
    assert "raw_payload" not in record.model_dump()
    assert persisted["raw"]["raw_payload"] == {"unbounded": "provider response"}
    assert persisted["structured"]["evidence_snapshot"]["raw_payload_ref"] == record.raw_payload_ref
