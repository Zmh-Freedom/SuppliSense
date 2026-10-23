from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.domains.alert import coverage_service, service as alert_service
from app.domains.risk import assessment_service
from tests.evidence_validity_fixtures import ENTITY_ID, NOW, SCOPE, source_evidence


ACTOR_ID = "user-fixture-a"
MONITOR_ID = "monitor-fixture-a"
COMPANY_NAME = "虚构甲供应链有限公司"
TARGET = {
    "monitor_target_id": MONITOR_ID,
    "company_id": ENTITY_ID,
    "company_name": COMPANY_NAME,
    "supplier_id": "supplier-fixture-a",
    "supplier_code": "fixture-code-a",
    "target_type": "formal_supplier",
    "identity_status": "verified",
    "purchase_scope": SCOPE,
}


class FakeCursor(list):
    def sort(self, key, direction=-1):
        if isinstance(key, list):
            for field, order in reversed(key):
                super().sort(key=lambda item: item.get(field) or "", reverse=order < 0)
        else:
            super().sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, count):
        return FakeCursor(self[:count])


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])
        self.queries = []

    def find_one(self, query=None, sort=None):
        self.queries.append(query or {})
        matches = [item for item in self.documents if _matches(item, query or {})]
        if sort:
            fields = sort if isinstance(sort, list) else [(sort, -1)]
            for field, direction in reversed(fields):
                matches.sort(key=lambda item: item.get(field) or "", reverse=direction < 0)
        return deepcopy(matches[0]) if matches else None

    def find(self, query=None):
        self.queries.append(query or {})
        return FakeCursor([deepcopy(item) for item in self.documents if _matches(item, query or {})])

    def count_documents(self, query=None):
        self.queries.append(query or {})
        return sum(_matches(item, query or {}) for item in self.documents)


class FakeDatabase(dict):
    def __getitem__(self, key):
        return super().get(key, FakeCollection())


def _matches(document, query):
    return all(document.get(key) == value for key, value in query.items())


def _manifest_entry(dimension, *, observed_at="2026-10-22T12:00:00Z", collected_at="2026-10-22T12:05:00Z", status="available", partial=False):
    return {
        **source_evidence(
            source=f"fixture-assessment-{dimension}",
            status=status,
            observed_at=observed_at,
            collected_at=collected_at,
            expires_at=None,
            query_receipt={"status": "success", "record_count": 0 if status == "empty" else 1},
            coverage_partial=partial,
        ),
        "dimension": dimension,
        "content_hash": f"fixture-content-{dimension}",
        "raw_payload_ref": f"fixture-payload-{dimension}",
        "conflict": "none",
    }


def _assessment_row(*, quality="complete", manifest=None):
    return {
        "id": "assessment-fixture-a",
        "assessment_id": "assessment-fixture-a",
        "assessment_status": "committed",
        "company_id": ENTITY_ID,
        "company_name": COMPANY_NAME,
        "monitor_target_id": MONITOR_ID,
        "scope_key": SCOPE,
        "actor_id": ACTOR_ID,
        "policy_hash": "fixture-policy-hash",
        "scoring_version": "fixture-score-v1",
        "quality": quality,
        "identity": {
            "company_id": ENTITY_ID,
            "company_name": COMPANY_NAME,
            "monitor_target_id": MONITOR_ID,
            "purchase_scope": SCOPE,
            "dimensions": ["financial", "judicial", "operational", "soft"],
        },
        "policy_snapshot": {
            "version": "fixture-policy-v1",
            "freshness_days": {"financial": 550, "judicial": 90, "operational": 90, "soft": 1},
        },
        "evidence_manifest": manifest if manifest is not None else [
            _manifest_entry(dimension)
            for dimension in ("financial", "judicial", "operational", "soft")
        ],
    }


def _snapshot(row=None, *, risk_level="低风险", risk_score=88):
    row = row or _assessment_row()
    return {
        "assessment_id": row["id"],
        "company_id": ENTITY_ID,
        "company_name": COMPANY_NAME,
        "monitor_target_id": MONITOR_ID,
        "assessment_scope_key": SCOPE,
        "policy_hash": row["policy_hash"],
        "scoring_version": row["scoring_version"],
        "quality": row["quality"],
        "checked_at": NOW,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "financial": {"debt_ratio": 0.2},
        "risk_detail": {"data_coverage": {"available_dimensions": ["financial"]}},
    }


def _install_assessment_context(monkeypatch, row):
    monkeypatch.setattr(
        coverage_service,
        "get_assessment_context",
        lambda: {"actor_id": ACTOR_ID, "actor_role": "analyst"},
    )
    monkeypatch.setattr(
        assessment_service,
        "get_monitor_assessment_context",
        lambda target, snapshot, *, actor_id, actor_role: {**deepcopy(row), "resolved_identity": {"company_id": ENTITY_ID, "company_name": COMPANY_NAME}},
    )


def test_2020_records_in_every_domain_do_not_make_current_coverage_complete(monkeypatch):
    old_manifest = [
        _manifest_entry(
            dimension,
            observed_at="2020-01-01T00:00:00Z",
            collected_at=NOW.isoformat(),
        )
        for dimension in ("financial", "judicial", "operational", "soft")
    ]
    row = _assessment_row(manifest=old_manifest)
    _install_assessment_context(monkeypatch, row)
    db = FakeDatabase()

    coverage = coverage_service.build_monitor_coverage(db, TARGET, _snapshot(row), now=NOW)

    assert coverage["status"] != "complete"
    risk = next(item for item in coverage["dimensions"] if item["key"] == "risk_snapshot")
    assert risk["status"] == "pending"
    assert "过期" in risk["detail"]


def test_new_collection_time_does_not_renew_old_source_observation(monkeypatch):
    old_manifest = [
        _manifest_entry(
            dimension,
            observed_at="2020-01-01T00:00:00Z",
            collected_at=NOW.isoformat(),
        )
        for dimension in ("financial", "judicial", "operational", "soft")
    ]
    row = _assessment_row(manifest=old_manifest)
    _install_assessment_context(monkeypatch, row)

    coverage = coverage_service.build_monitor_coverage(FakeDatabase(), TARGET, _snapshot(row), now=NOW)

    financial = next(item for item in coverage["dimensions"] if item["key"] == "financial")
    assert financial["status"] == "pending"
    assert financial["validity"]["status"] == "stale"


def test_partial_assessment_quality_cannot_count_as_a_current_risk_snapshot(monkeypatch):
    row = _assessment_row(quality="partial")
    _install_assessment_context(monkeypatch, row)

    coverage = coverage_service.build_monitor_coverage(FakeDatabase(), TARGET, _snapshot(row), now=NOW)

    risk = next(item for item in coverage["dimensions"] if item["key"] == "risk_snapshot")
    assert risk["status"] == "pending"
    assert "部分" in risk["detail"] or "不完整" in risk["detail"]


def test_current_complete_frozen_manifest_counts_as_current_snapshot_evidence(monkeypatch):
    row = _assessment_row()
    _install_assessment_context(monkeypatch, row)

    coverage = coverage_service.build_monitor_coverage(FakeDatabase(), TARGET, _snapshot(row), now=NOW)

    risk = next(item for item in coverage["dimensions"] if item["key"] == "risk_snapshot")
    financial = next(item for item in coverage["dimensions"] if item["key"] == "financial")
    assert risk["status"] == "available"
    assert risk["validity"]["status"] == "available"
    assert financial["status"] == "available"


def test_partial_manifest_cannot_count_as_a_complete_risk_snapshot(monkeypatch):
    manifest = [
        _manifest_entry(dimension, partial=dimension == "judicial")
        for dimension in ("financial", "judicial", "operational", "soft")
    ]
    row = _assessment_row(manifest=manifest)
    _install_assessment_context(monkeypatch, row)

    coverage = coverage_service.build_monitor_coverage(FakeDatabase(), TARGET, _snapshot(row), now=NOW)

    risk = next(item for item in coverage["dimensions"] if item["key"] == "risk_snapshot")
    assert risk["status"] == "pending"
    assert risk["validity"]["status"] != "available"


def test_financial_numbers_without_an_authorized_manifest_are_not_current_coverage(monkeypatch):
    monkeypatch.setattr(coverage_service, "get_assessment_context", lambda: {})
    stale_snapshot = {
        **_snapshot(),
        "financial": {"debt_ratio": 0.1},
        "risk_detail": {"data_coverage": {"available_dimensions": ["financial"]}},
    }

    coverage = coverage_service.build_monitor_coverage(FakeDatabase(), TARGET, stale_snapshot, now=NOW)

    financial = next(item for item in coverage["dimensions"] if item["key"] == "financial")
    assert financial["status"] == "pending"
    assert financial["validity"]["status"] != "available"


def test_assessment_identity_is_rechecked_before_pg_read(monkeypatch):
    calls = []
    monkeypatch.setattr(assessment_service, "resolve_identity", lambda *args, **kwargs: calls.append("resolve") or {"company_id": ENTITY_ID, "company_name": COMPANY_NAME, "monitor_target_id": MONITOR_ID, "purchase_scope": SCOPE})
    monkeypatch.setattr(assessment_service.repo, "get_assessment", lambda assessment_id, actor_id, *, allow_all=False: calls.append((assessment_id, actor_id, allow_all)) or _assessment_row())

    result = assessment_service.get_monitor_assessment_context(
        TARGET,
        _snapshot(),
        actor_id=ACTOR_ID,
        actor_role="analyst",
    )

    assert calls[0] == "resolve"
    assert calls[1] == ("assessment-fixture-a", ACTOR_ID, False)
    assert result["resolved_identity"]["company_id"] == ENTITY_ID


def test_assessment_context_without_actor_does_not_read_pg(monkeypatch):
    calls = []
    monkeypatch.setattr(assessment_service.repo, "get_assessment", lambda *args, **kwargs: calls.append(args))

    result = assessment_service.get_monitor_assessment_context(
        TARGET,
        _snapshot(),
        actor_id="",
        actor_role="",
    )

    assert result is None
    assert calls == []


def test_assessment_row_for_another_monitor_target_is_rejected(monkeypatch):
    monkeypatch.setattr(assessment_service, "resolve_identity", lambda *args, **kwargs: {"company_id": ENTITY_ID, "company_name": COMPANY_NAME, "monitor_target_id": MONITOR_ID, "purchase_scope": SCOPE})
    other_target_row = _assessment_row()
    other_target_row["monitor_target_id"] = "monitor-fixture-other"
    monkeypatch.setattr(assessment_service.repo, "get_assessment", lambda *args, **kwargs: other_target_row)

    result = assessment_service.get_monitor_assessment_context(
        TARGET,
        _snapshot(),
        actor_id=ACTOR_ID,
        actor_role="analyst",
    )

    assert result is None


def test_assessment_store_failure_is_exposed_without_name_fallback(monkeypatch):
    monkeypatch.setattr(assessment_service, "resolve_identity", lambda *args, **kwargs: {"company_id": ENTITY_ID, "company_name": COMPANY_NAME, "monitor_target_id": MONITOR_ID, "purchase_scope": SCOPE})
    monkeypatch.setattr(assessment_service.repo, "get_assessment", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("fixture PG timeout")))

    with pytest.raises(TimeoutError):
        assessment_service.get_monitor_assessment_context(
            TARGET,
            _snapshot(),
            actor_id=ACTOR_ID,
            actor_role="analyst",
        )


def test_pg_timeout_keeps_projection_historical_and_does_not_read_name_keyed_sources(monkeypatch):
    class TrackingDatabase(FakeDatabase):
        def __init__(self):
            super().__init__()
            self.accessed = []

        def __getitem__(self, key):
            self.accessed.append(key)
            return super().__getitem__(key)

    db = TrackingDatabase()
    monkeypatch.setattr(
        coverage_service,
        "get_assessment_context",
        lambda: {"actor_id": ACTOR_ID, "actor_role": "analyst"},
    )
    monkeypatch.setattr(
        assessment_service,
        "get_monitor_assessment_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("fixture PG timeout")),
    )

    coverage = coverage_service.build_monitor_coverage(db, TARGET, _snapshot(), now=NOW)

    risk = next(item for item in coverage["dimensions"] if item["key"] == "risk_snapshot")
    assert risk["status"] == "pending"
    assert risk["validity"]["status"] == "failed"
    assert "evidence_manifest" not in risk["facts"]
    assert db.accessed == []


def test_revoked_assessment_access_does_not_fall_back_to_name_keyed_sources(monkeypatch):
    class TrackingDatabase(FakeDatabase):
        def __init__(self):
            super().__init__()
            self.accessed = []

        def __getitem__(self, key):
            self.accessed.append(key)
            return super().__getitem__(key)

    db = TrackingDatabase()
    reads = []
    monkeypatch.setattr(
        coverage_service,
        "get_assessment_context",
        lambda: {"actor_id": ACTOR_ID, "actor_role": "analyst"},
    )
    monkeypatch.setattr(
        assessment_service,
        "resolve_identity",
        lambda *args, **kwargs: {
            "company_id": ENTITY_ID,
            "company_name": COMPANY_NAME,
            "monitor_target_id": MONITOR_ID,
            "purchase_scope": SCOPE,
        },
    )
    monkeypatch.setattr(
        assessment_service.repo,
        "get_assessment",
        lambda assessment_id, actor_id, *, allow_all=False: reads.append(
            (assessment_id, actor_id, allow_all)
        ) or None,
    )

    coverage = coverage_service.build_monitor_coverage(db, TARGET, _snapshot(), now=NOW)

    risk = next(item for item in coverage["dimensions"] if item["key"] == "risk_snapshot")
    assert reads == [("assessment-fixture-a", ACTOR_ID, False)]
    assert risk["status"] == "pending"
    assert risk["validity"]["status"] == "unknown"
    assert not risk["validity"].get("facts", {}).get("dimensions")
    assert db.accessed == []


def test_sentiment_is_stale_at_the_existing_six_hour_boundary():
    cached = {
        "company_name": COMPANY_NAME,
        "analyzed_at": NOW - timedelta(hours=6),
        "articles": [{"title": f"{COMPANY_NAME} fixture update", "body": "fixture"}],
        "articles_count": 1,
    }
    db = FakeDatabase({"sentiment_results": FakeCollection([cached])})

    validity = coverage_service.read_sentiment_validity(
        db, {"company_name": COMPANY_NAME, "company_id": ENTITY_ID}, now=NOW
    )

    assert validity["status"] == "stale"


def test_sentiment_uses_latest_analyzed_result_and_empty_cache_waits_for_refresh():
    newest = {
        "company_name": COMPANY_NAME,
        "analyzed_at": NOW - timedelta(hours=1),
        "articles": [],
        "articles_count": 0,
    }
    older = {
        "company_name": COMPANY_NAME,
        "analyzed_at": NOW - timedelta(hours=2),
        "articles": [{"title": f"{COMPANY_NAME} older fixture news", "body": "fixture"}],
        "articles_count": 1,
    }
    db = FakeDatabase({"sentiment_results": FakeCollection([older, newest])})

    validity = coverage_service.read_sentiment_validity(
        db, {"company_name": COMPANY_NAME, "company_id": ENTITY_ID}, now=NOW
    )

    assert validity["status"] == "unknown"
    assert "复查" in validity["detail"] or "空" in validity["detail"]


def test_sentiment_relevance_failure_does_not_count_as_available(monkeypatch):
    cached = {
        "company_name": COMPANY_NAME,
        "analyzed_at": NOW - timedelta(minutes=15),
        "articles": [{"title": "unrelated fixture story", "body": "unrelated"}],
        "articles_count": 1,
    }
    db = FakeDatabase({"sentiment_results": FakeCollection([cached])})
    monkeypatch.setattr("app.domains.risk.sentiment._cached_sentiment_is_relevant", lambda *args, **kwargs: False)

    validity = coverage_service.read_sentiment_validity(
        db, {"company_name": COMPANY_NAME, "company_id": ENTITY_ID}, now=NOW
    )

    assert validity["status"] == "unknown"


def test_current_transaction_row_without_accepted_freshness_policy_is_pending():
    row = {
        "supplier_id": "supplier-fixture-a",
        "supplier_code": "fixture-code-a",
        "sync_status": "current",
        "data_mode": "real",
        "eligible_for_formal_assessment": True,
        "data_quality_status": "valid",
        "snapshot_month": "2026-09",
    }
    db = FakeDatabase({"supplier_transaction_snapshots": FakeCollection([row])})

    validity = coverage_service.read_transaction_validity(
        db, TARGET, now=NOW
    )

    assert validity["status"] == "unknown"
    assert "政策" in validity["detail"] or "期限" in validity["detail"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"data_mode": "synthetic"},
        {"eligible_for_formal_assessment": False},
        {"data_quality_status": "unknown"},
    ],
)
def test_synthetic_ineligible_or_unknown_transaction_data_is_not_available(overrides):
    row = {
        "supplier_id": "supplier-fixture-a",
        "supplier_code": "fixture-code-a",
        "sync_status": "current",
        "data_mode": "real",
        "eligible_for_formal_assessment": True,
        "data_quality_status": "valid",
        "snapshot_month": "2026-09",
        **overrides,
    }
    db = FakeDatabase({"supplier_transaction_snapshots": FakeCollection([row])})

    validity = coverage_service.read_transaction_validity(db, TARGET, now=NOW)

    assert validity["status"] != "available"


def test_latest_bad_transaction_month_does_not_fall_back_to_older_current_month():
    older = {
        "supplier_id": "supplier-fixture-a",
        "supplier_code": "fixture-code-a",
        "sync_status": "current",
        "data_mode": "real",
        "eligible_for_formal_assessment": True,
        "data_quality_status": "valid",
        "snapshot_month": "2026-08",
    }
    latest_bad = {
        "supplier_id": "supplier-fixture-a",
        "supplier_code": "fixture-code-a",
        "sync_status": "failed",
        "data_mode": "real",
        "eligible_for_formal_assessment": False,
        "snapshot_month": "2026-09",
    }
    db = FakeDatabase({"supplier_transaction_snapshots": FakeCollection([older, latest_bad])})

    validity = coverage_service.read_transaction_validity(db, TARGET, now=NOW)

    assert validity["status"] in {"failed", "unknown"}
    assert validity.get("facts", {}).get("snapshot_month") == "2026-09"


def test_intake_calls_part_relationships_historical_and_separate_from_current_supply(monkeypatch):
    db = FakeDatabase({"internal_supplier_material_relations": FakeCollection([{"supplier_code": "fixture-code-a"}])})
    monkeypatch.setattr("app.domains.risk.repo_financial.get_financial_metrics", lambda _name: None)
    candidate = {
        "legal_name": COMPANY_NAME,
        "company_id": ENTITY_ID,
        "supplier_id": TARGET["supplier_id"],
        "supplier_code": TARGET["supplier_code"],
        "verification_status": "verified",
    }

    dimensions, findings, evidence = coverage_service.build_intake_coverage(
        db,
        candidate,
        COMPANY_NAME,
        {"status": "unavailable", "detail": "no formal receipt"},
        now=NOW,
    )

    history = next(item for item in dimensions if item["key"] == "history")
    transaction = next(item for item in dimensions if item["key"] == "transaction")
    assert history["status"] == "available"
    assert "历史" in history["detail"]
    assert transaction["status"] != "available"
    assert any("历史" in item["title"] for item in findings)
    assert evidence


def test_historical_high_risk_stays_priority_review_and_marks_recheck_needed():
    coverage = {
        "status": "partial",
        "dimensions": [{"key": "risk_snapshot", "status": "pending", "detail": "来源已过期"}],
    }

    action = alert_service._next_action(
        {"identity_status": "verified"},
        {"risk_level": "高风险"},
        {"status": "stable"},
        coverage,
    )

    assert action["code"] == "review"
    assert action["priority"] == "high"
    assert "历史" in action["reason"]


def test_low_risk_with_incomplete_coverage_supplements_and_complete_current_state_can_continue():
    incomplete = alert_service._next_action(
        {"identity_status": "verified"},
        {"risk_level": "低风险"},
        {"status": "stable"},
        {"status": "partial", "dimensions": []},
    )
    complete = alert_service._next_action(
        {"identity_status": "verified"},
        {"risk_level": "低风险"},
        {"status": "stable"},
        {"status": "complete", "dimensions": []},
    )

    assert incomplete["code"] == "supplement_data"
    assert complete["code"] == "continue_monitoring"
