"""Deterministic evidence usability rules, independent of database state."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.domains.risk.evidence_validity import evaluate_evidence_validity
from tests.evidence_validity_fixtures import ENTITY_ID, NOW, SCOPE, source_evidence


def evaluate(evidence: dict, **kwargs: object) -> dict:
    return evaluate_evidence_validity(
        evidence,
        expected_entity_id=ENTITY_ID,
        expected_scope=SCOPE,
        now=NOW,
        **kwargs,
    )


def test_current_formal_source_is_available():
    assert evaluate(source_evidence())["status"] == "available"


def test_completed_zero_result_is_empty():
    evidence = source_evidence(
        status="empty", query_receipt={"status": "success", "record_count": 0}
    )

    assert evaluate(evidence)["status"] == "empty"


@pytest.mark.parametrize(
    "evidence",
    [
        source_evidence(status="missing"),
        source_evidence(source=None, status="missing"),
    ],
)
def test_missing_source_is_missing_without_invented_freshness(evidence):
    assert evaluate(evidence)["status"] == "missing"


def test_expiry_boundary_is_stale():
    evidence = source_evidence(expires_at=NOW.isoformat())

    result = evaluate(evidence)

    assert result["status"] == "stale"


def test_recent_collection_does_not_refresh_old_source_observation():
    evidence = source_evidence(
        observed_at="2026-10-01T00:00:00Z",
        collected_at=NOW.isoformat(),
        expires_at=None,
    )

    result = evaluate(
        evidence,
        max_age=timedelta(days=14),
        policy_version="fixture-policy-v1",
    )

    assert result["status"] == "stale"
    assert result["observed_at"] == "2026-10-01T00:00:00+00:00"
    assert result["collected_at"] == NOW.isoformat()


@pytest.mark.parametrize(
    "overrides",
    [
        {"observed_at": None},
        {"observed_at": "not-a-timestamp"},
        {"observed_at": "2026-10-24T00:00:00Z"},
        {"observed_at": "2026-10-22T00:00:00Z", "expires_at": "2026-10-21T00:00:00Z"},
        {"source_version": None},
        {"source_mode": "sample"},
        {"coverage_partial": True},
    ],
)
def test_uncertain_metadata_cannot_produce_current_evidence(overrides):
    assert evaluate(source_evidence(**overrides))["status"] == "unknown"


def test_missing_required_freshness_policy_version_is_unknown():
    assert evaluate(source_evidence(expires_at=None))["status"] == "unknown"
    assert evaluate(
        source_evidence(expires_at=None), max_age=timedelta(days=1)
    )["status"] == "unknown"


@pytest.mark.parametrize("max_age", [timedelta(0), timedelta(days=-1)])
def test_non_positive_max_age_is_unknown(max_age):
    assert evaluate(
        source_evidence(expires_at=None),
        max_age=max_age,
        policy_version="fixture-policy-v1",
    )["status"] == "unknown"


def test_earliest_expiry_applies_when_explicit_and_policy_expiry_exist():
    evidence = source_evidence(expires_at="2026-10-22T12:00:00Z")

    result = evaluate(
        evidence,
        max_age=timedelta(days=1),
        policy_version="fixture-policy-v1",
    )

    assert result["status"] == "stale"
    assert result["expires_at"] == "2026-10-22T12:00:00+00:00"


def test_policy_expiry_is_returned_when_it_is_earlier():
    result = evaluate(
        source_evidence(expires_at="2026-10-24T00:00:00Z"),
        max_age=timedelta(hours=36),
        policy_version="fixture-policy-v1",
    )

    assert result["status"] == "available"
    assert result["expires_at"] == "2026-10-23T12:00:00+00:00"


@pytest.mark.parametrize(
    ("source_status", "receipt"),
    [
        ("failed", {"status": "success", "record_count": 1}),
        ("conflicting", {"status": "success", "record_count": 1}),
    ],
)
def test_explicit_source_failure_or_conflict_is_preserved(source_status, receipt):
    result = evaluate(
        source_evidence(status=source_status, query_receipt=receipt)
    )

    assert result["status"] == source_status


def test_entity_or_scope_mismatch_is_conflicting():
    assert evaluate(source_evidence(entity_id="company-fixture-b"))["status"] == "conflicting"
    assert evaluate(source_evidence(scope="another-scope"))["status"] == "conflicting"


def test_equivalent_timezone_instants_have_same_validity():
    utc = evaluate(source_evidence(expires_at="2026-10-24T00:00:00Z"))
    offset = evaluate(source_evidence(expires_at="2026-10-24T08:00:00+08:00"))

    assert utc["status"] == offset["status"] == "available"
    assert utc["expires_at"] == offset["expires_at"] == "2026-10-24T00:00:00+00:00"


def test_naive_timestamps_are_interpreted_as_utc():
    result = evaluate(
        source_evidence(
            observed_at=datetime(2026, 10, 22),
            collected_at=datetime(2026, 10, 22, 0, 5),
            expires_at=datetime(2026, 10, 24),
        )
    )

    assert result["status"] == "available"
    assert result["observed_at"] == "2026-10-22T00:00:00+00:00"


def test_validity_evaluation_does_not_mutate_source_evidence():
    evidence = source_evidence()
    before = deepcopy(evidence)

    evaluate(evidence)

    assert evidence == before


def test_invalid_success_receipt_is_unknown():
    for receipt in (
        {"status": "failed", "record_count": 1},
        {"status": "success", "record_count": 0},
        {"status": "success", "record_count": True},
        {"status": "success"},
    ):
        assert evaluate(source_evidence(query_receipt=receipt))["status"] == "unknown"


def test_empty_requires_successful_zero_count_receipt():
    for receipt in (
        {"status": "failed", "record_count": 0},
        {"status": "success", "record_count": 1},
        None,
    ):
        assert evaluate(
            source_evidence(status="empty", query_receipt=receipt)
        )["status"] == "unknown"


def test_naive_now_is_interpreted_as_utc():
    evidence = source_evidence(expires_at="2026-10-23T13:00:00Z")

    result = evaluate_evidence_validity(
        evidence,
        expected_entity_id=ENTITY_ID,
        expected_scope=SCOPE,
        now=datetime(2026, 10, 23, 12),
    )

    assert result["status"] == "available"
