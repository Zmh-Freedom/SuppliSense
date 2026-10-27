from datetime import datetime, timedelta, timezone

import pytest

from app.domains.risk.assessment_evidence import provider_receipt, make_evidence
from app.domains.risk.assessment_contracts import canonical_hash
from app.domains.risk.assessment_policy import current_policy


def test_error_payload_is_unavailable_instead_of_successful_empty():
    receipt = provider_receipt({"items": {"error_code": 404, "result": {"total": 0}}})
    assert receipt["status"] == "unavailable"
    assert provider_receipt({"items": {"result": {}}})["status"] == "unavailable"
    assert provider_receipt({"items": {"result": {"total": 0}}}) == {
        "status": "success",
        "record_count": 0,
    }


def test_raw_payload_is_content_addressed_and_does_not_fabricate_source_time():
    row = {
        "id": "assessment",
        "company_id": "company",
        "run_id": None,
        "collector_token": "owner",
    }
    payload = {"value": 2}
    entry, raw = make_evidence(
        row,
        "financial",
        payload,
        status="available",
        source="provider",
        policy=current_policy(),
    )
    assert entry["content_hash"] == canonical_hash(payload)
    assert entry["observed_at"] is None
    assert entry["coverage_partial"]
    assert raw["assessment_id"] == row["id"]
    assert raw["raw_payload"] == payload


def test_expired_source_is_marked_stale_before_scoring():
    row = {
        "id": "assessment",
        "company_id": "company",
        "run_id": None,
        "collector_token": "owner",
    }
    entry, _ = make_evidence(
        row,
        "judicial",
        {"count": 5},
        status="available",
        source="provider",
        policy=current_policy(),
        observed_at=datetime.now(timezone.utc) - timedelta(days=100),
    )
    assert entry["status"] == "stale"


def test_future_observation_is_unavailable_and_never_marked_fresh(monkeypatch):
    from app.domains.risk import assessment_evidence as evidence

    now = datetime(2026, 10, 27, 4, 0, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(evidence, "datetime", FrozenDatetime)
    row = {
        "id": "assessment",
        "company_id": "company",
        "run_id": None,
        "collector_token": "owner",
    }

    entry, _ = evidence.make_evidence(
        row,
        "judicial",
        {"count": 5},
        status="available",
        source="provider",
        policy=current_policy(),
        observed_at=now + timedelta(minutes=1),
    )

    assert entry["status"] == "unavailable"
    assert entry["freshness"] == "unknown"
    assert entry["coverage_partial"] is True


def test_unparseable_observation_is_unavailable_not_scoreable():
    row = {
        "id": "assessment",
        "company_id": "company",
        "run_id": None,
        "collector_token": "owner",
    }

    entry, _ = make_evidence(
        row,
        "judicial",
        {"count": 5},
        status="available",
        source="provider",
        policy=current_policy(),
        observed_at="not-a-timestamp",
    )

    assert entry["status"] == "unavailable"
    assert entry["freshness"] == "unknown"
    assert entry["coverage_partial"] is True


def test_missing_source_timestamp_does_not_hide_known_staleness(monkeypatch):
    from datetime import timedelta
    from unittest.mock import MagicMock
    from app.domains.risk import assessment_evidence as evidence
    from app.schemas.company import CompanyProfile
    from app.schemas.risk import RiskInfo

    profile = CompanyProfile(
        company_name="公司",
        legal_person="",
        registered_capital="",
        establish_time="",
        industry="制造业",
        is_listed=False,
    )
    db = {
        source: MagicMock()
        for source in (
            *evidence.JUDICIAL_SOURCES,
            *evidence.OPERATIONAL_SOURCES,
            "riskInfo",
            "lawSuit_detail",
            "financial_cache",
        )
    }
    for collection in db.values():
        collection.find_one.return_value = None
    db["lawSuit"].find_one.return_value = {
        "name": "公司",
        "items": {"result": {"total": 1}},
        "fetched_at": datetime.now(timezone.utc) - timedelta(days=100),
    }
    monkeypatch.setattr(evidence, "get_db", lambda: db)
    monkeypatch.setattr(
        "app.domains.risk.service.get_risk_info",
        lambda _: RiskInfo(
            lawsuit_count=1,
            executed_count=0,
            abnormal_operation_count=0,
            administrative_penalty_count=0,
        ),
    )
    monkeypatch.setattr("app.domains.risk.service.get_risk_indicators", lambda _: {})
    monkeypatch.setattr(
        "app.domains.risk.service.get_recent_lawsuits", lambda *a, **k: 1
    )
    _, manifest, _ = evidence.collect_inputs(
        {
            "id": "assessment",
            "company_id": "company",
            "collector_token": "owner",
            "policy_snapshot": current_policy(["judicial"]),
        },
        profile,
        include_soft=False,
    )
    assert manifest[0]["status"] == "stale"
