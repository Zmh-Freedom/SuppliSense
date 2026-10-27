"""Transport and monitoring boundaries for the durable assessment ledger."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domains.alert import service as alerts
from app.services import scheduler
from app.schemas import FinancialMetrics


def test_legacy_fallback_rejects_ambiguous_name(monkeypatch):
    db = {
        name: MagicMock()
        for name in (
            "watchlist",
            "financial_evidence_snapshots",
            "alert_snapshots",
            "alerts",
        )
    }
    db["watchlist"].count_documents.return_value = 2
    monkeypatch.setattr(alerts, "get_db", lambda: db)
    assert alerts._legacy_snapshot_query(db, "同名公司") is None
    db["watchlist"].count_documents.return_value = 1
    query = alerts._legacy_snapshot_query(db, "同名公司")
    assert query["monitor_target_id"] is None
    assert query["company_id"] is None


def test_change_comparison_rejects_different_policy_or_scope():
    first = {
        "risk_score": 80,
        "policy_hash": "p1",
        "assessment_scope_key": "s1",
        "actor_id": "a",
        "company_id": "c",
    }
    for changed in (
        {"policy_hash": "p2"},
        {"actor_id": "b"},
        {"assessment_scope_key": "s2"},
        {"company_id": "d"},
    ):
        assert not alerts._comparable_snapshots(first, {**first, **changed})
    assert alerts._comparable_snapshots(first, dict(first))
    assert not alerts._comparable_snapshots(first, {"risk_score": 90})


@pytest.mark.parametrize(
    ("target_identity", "requested_company_id"),
    [
        ({"identity_status": "candidate"}, "unrelated-company"),
        (
            {"identity_status": "candidate", "company_id": "candidate-company"},
            "candidate-company",
        ),
        (
            {"identity_status": "verified", "company_id": "candidate-company"},
            "candidate-company",
        ),
    ],
)
def test_assessment_rejects_binding_unverified_target_to_other_company(
    monkeypatch, target_identity, requested_company_id
):
    from app.core.errors import DomainError
    from app.domains.alert import service as alert_service
    from app.domains.risk import assessment_service
    from app.domains.supplier import access

    monkeypatch.setattr(
        alert_service,
        "_find_watchlist_target",
        lambda **_kwargs: {
            "monitor_target_id": "candidate-target",
            "target_type": "external_candidate",
            "candidate_id": "candidate-1",
            "company_name": "候选供应商",
            **target_identity,
        },
    )
    monkeypatch.setattr(alert_service, "_can_access_target", lambda *_args: True)
    monkeypatch.setattr(
        assessment_service.company_service,
        "get_company",
        lambda company_id: {
            "id": company_id,
            "legal_name": "无关已核验企业",
            "verification_status": "verified",
        },
    )
    monkeypatch.setattr(
        assessment_service.company_service,
        "search_identity",
        lambda _name: {
            "resolution": "exact",
            "exact": {
                "company_id": "name-matched-company",
                "verification_status": "verified",
            },
        },
    )
    monkeypatch.setattr(access, "formal_supplier_id_by_name", lambda _name: None)

    with pytest.raises(DomainError) as exc:
        assessment_service.resolve_identity(
            "无关已核验企业",
            company_id=requested_company_id,
            monitor_target_id="candidate-target",
            actor_id="owner",
            actor_role="purchaser",
            dimensions=["financial"],
        )

    assert exc.value.code == "ASSESSMENT_IDENTITY_REQUIRED"
    assert exc.value.status_code == 409


def test_assessment_accepts_verified_target_with_matching_canonical_identity(monkeypatch):
    from app.domains.alert import service as alert_service
    from app.domains.risk import assessment_service
    from app.domains.supplier import access

    target = {
        "monitor_target_id": "verified-target",
        "target_type": "formal_supplier",
        "identity_status": "verified",
        "company_id": "verified-company",
        "company_name": "已核验供应商有限公司",
    }
    canonical = {
        "id": "verified-company",
        "legal_name": "已核验供应商有限公司",
        "verification_status": "verified",
    }
    monkeypatch.setattr(alert_service, "_find_watchlist_target", lambda **_kwargs: target)
    monkeypatch.setattr(alert_service, "_can_access_target", lambda *_args: True)
    monkeypatch.setattr(assessment_service.company_service, "get_company", lambda _id: canonical)
    monkeypatch.setattr(
        assessment_service.company_service,
        "search_identity",
        lambda _name: {
            "resolution": "exact",
            "exact": {
                "company_id": "verified-company",
                "verification_status": "verified",
            },
        },
    )
    monkeypatch.setattr(access, "formal_supplier_id_by_name", lambda _name: None)

    identity = assessment_service.resolve_identity(
        "已核验供应商有限公司",
        company_id="verified-company",
        monitor_target_id="verified-target",
        actor_id="owner",
        actor_role="purchaser",
        dimensions=["financial"],
    )

    assert identity["company_id"] == "verified-company"
    assert identity["monitor_target_id"] == "verified-target"


def test_financial_refresh_preserves_aggregate_assessment(monkeypatch):
    db = {
        name: MagicMock()
        for name in (
            "watchlist",
            "financial_evidence_snapshots",
            "alert_snapshots",
            "alerts",
        )
    }
    db["financial_evidence_snapshots"].find_one.return_value = None
    financial = FinancialMetrics(
        debt_ratio=0.8, revenue_growth=-0.1, net_profit_growth=-0.2, cash_flow=0
    )
    original = {
        "assessment_id": "original",
        "risk_score": 80,
        "checked_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "financial": {"debt_ratio": 0.3},
    }
    monkeypatch.setattr(
        scheduler,
        "get_watchlist_targets",
        lambda: [
            {
                "company_name": "公司",
                "monitor_target_id": "target",
                "company_id": "company",
            }
        ],
    )
    monkeypatch.setattr(
        scheduler, "get_baseinfo", lambda _: SimpleNamespace(is_listed=True)
    )
    monkeypatch.setattr(scheduler, "get_financial_metrics", lambda _: financial)
    monkeypatch.setattr(scheduler, "get_latest_snapshot", lambda *a, **k: original)
    monkeypatch.setattr("app.db.mongo.get_db", lambda: db)
    monkeypatch.setattr("app.domains.alert.rules.get_rules", lambda *_: [])
    monkeypatch.setattr("app.domains.alert.rules.evaluate_changes", lambda *_: [])
    scheduler.run_financial_check()
    assert not db["alert_snapshots"].insert_one.called
    assert not db["alert_snapshots"].update_one.called
    document = db["financial_evidence_snapshots"].update_one.call_args.args[1][
        "$setOnInsert"
    ]
    assert document["previous_assessment_id"] == "original"
    assert "risk_score" not in document and "assessed_at" not in document


def test_paid_refresh_batch_uses_injected_user_scope_and_actor(monkeypatch):
    targets = [{"company_name": "甲公司", "monitor_target_id": "target-1"}]
    calls = []
    monkeypatch.setattr(
        scheduler,
        "get_watchlist_targets",
        lambda: pytest.fail("explicitly scoped refresh must not reload global targets"),
    )
    monkeypatch.setattr(
        scheduler,
        "run_refresh_for_target",
        lambda target, actor_id: calls.append((target, actor_id)) or {"company": target["company_name"]},
    )

    result = scheduler.run_refresh_all(targets=targets, actor_id="user-1")

    assert calls == [(targets[0], "user-1")]
    assert result == {"checked": 1, "channel": "paid", "alerts": [{"company": "甲公司"}]}


def test_trend_tool_uses_pg_scope_and_safety_direction(monkeypatch):
    from app.domains.risk import tools_analysis
    from app.tools.executor import ToolContext

    monkeypatch.setattr(
        "app.tools.executor.get_active_tool_context",
        lambda: ToolContext(user_id="actor"),
    )
    monkeypatch.setattr(
        "app.domains.risk.assessment_service._actor", lambda *a: ("actor", "purchaser")
    )
    monkeypatch.setattr(
        "app.domains.risk.assessment_service.comparable_trend",
        lambda *a, **k: [
            {"date": "2026-09-01", "risk_score": 40},
            {"date": "2026-10-01", "risk_score": 80},
        ],
    )
    value = tools_analysis.analyze_trend.invoke({"company_name": "公司"})
    assert value["trend"] == "改善"


def test_authenticated_name_history_reads_new_pg_assessments(monkeypatch):
    from app.domains.risk import assessment_service as app
    from app.domains.risk.assessment_contracts import assessment_scope
    from app.domains.risk.assessment_policy import current_policy

    row = {
        "id": "stable-assessment",
        "company_id": "company",
        "company_name": "公司",
        "actor_id": "actor",
        "monitor_target_id": None,
        "identity": {"dimensions": list(current_policy()["required_dimensions"])},
        "assessment_status": "committed",
        "quality": "partial",
        "result": {"risk_score": 37, "risk_level": "高风险"},
        "assessed_at": datetime.now(timezone.utc),
        "assessment_version": 1,
        "previous_assessment_id": None,
        "policy_hash": "policy",
        "scoring_version": "v3_evidence_1",
        "scope_key": "scope",
    }
    monkeypatch.setattr(
        app, "resolve_identity", lambda *a, **k: {"company_id": "company"}
    )
    monkeypatch.setattr(app.repo, "list_assessments", lambda *a, **k: [row])
    db = {name: MagicMock() for name in ("watchlist", "alert_snapshots")}
    db["watchlist"].count_documents.return_value = 0
    db["alert_snapshots"].find.return_value.sort.return_value.limit.return_value = []
    monkeypatch.setattr(alerts, "get_db", lambda: db)
    with assessment_scope(actor_id="actor", actor_role="purchaser"):
        history = alerts.get_snapshot_history("公司")
        assert history[0]["snapshot_id"] == "stable-assessment"
        assert alerts.get_latest_snapshot("公司")["risk_score"] == 37
