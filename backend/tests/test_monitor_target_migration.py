import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domains.alert import service as alert_service
from app.domains.alert import tools as alert_tools
from app.graphs.agent_supervisor.agents import AgentTaskContext, _run_risk
from app.graphs.agent_supervisor.contracts import PlannerTask
from app.schemas import RiskCalculateResponse
from app.tools.contracts import WatchlistOutput


def test_watchlist_output_accepts_owner_user_id_from_responsibility_scope():
    result = WatchlistOutput.model_validate({
        "company_name": "责任范围供应商有限公司",
        "owner_user_id": "purchaser-1",
        "monitor_status": "active",
    })

    assert result.owner_user_id == "purchaser-1"


def test_add_watchlist_persists_full_stable_monitor_target(monkeypatch):
    db = {name: MagicMock() for name in ("suppliers", "watchlist", "alerts")}
    suppliers = db["suppliers"]
    watchlist = db["watchlist"]
    suppliers.find_one.return_value = {
        "_id": "supplier-1",
        "name": "正式供应商有限公司",
        "company_id": "company-1",
        "supplier_code": "SUP-001",
    }
    watchlist.find_one.return_value = None
    monkeypatch.setattr(alert_service, "get_db", lambda: db)
    monkeypatch.setattr(alert_service, "_broadcast_alert_update", lambda: None)

    target = alert_service.add_to_watchlist(
        supplier_id="supplier-1",
        target_type="formal_supplier",
    )

    assert target["monitor_target_id"]
    assert target["supplier_id"] == "supplier-1"
    assert target["company_id"] == "company-1"
    assert target["target_type"] == "formal_supplier"
    assert watchlist.update_one.call_args.args[0] == {"supplier_id": "supplier-1"}


def test_add_watchlist_reuses_legacy_name_row_before_supplier_resolution(monkeypatch):
    """A retry must update the existing name-keyed row instead of colliding with its unique index."""
    db = {name: MagicMock() for name in ("suppliers", "watchlist", "alerts")}
    existing = {
        "monitor_target_id": "monitor-1",
        "company_name": "北京经纬恒润科技股份有限公司",
        "target_type": "company",
        "identity_status": "unresolved",
    }
    monkeypatch.setattr(alert_service, "get_db", lambda: db)
    monkeypatch.setattr(alert_service, "_find_watchlist_target", lambda **_: existing)
    monkeypatch.setattr(alert_service, "_broadcast_alert_update", lambda: None)

    def unexpected_resolution(*_args, **_kwargs):
        raise AssertionError("legacy monitor row should be reused before resolving the supplier name")

    monkeypatch.setattr(
        "app.domains.sourcing.supplier_repo.resolve_supplier_id",
        unexpected_resolution,
    )

    result = alert_service.add_to_watchlist("北京经纬恒润科技股份有限公司")

    assert result["monitor_target_id"] == "monitor-1"
    assert result["identity_status"] == "unresolved"
    assert db["watchlist"].update_one.call_args.args[0] == {
        "company_name": "北京经纬恒润科技股份有限公司"
    }


def test_monitor_target_risk_detail_returns_auditable_snapshot(monkeypatch):
    target = {"monitor_target_id": "monitor-risk-1", "company_name": "青岛三祥科技股份有限公司"}
    snapshot = {
        "snapshot_id": "snapshot-1", "snapshot_version": 1, "risk_score": 7, "risk_level": "低风险",
        "checked_at": "2026-09-09T08:00:00+00:00", "score_breakdown": {"财务风险": {"归一化": 3.4}},
        "risk_detail": {"lawsuit_count": 20}, "financial": {"net_profit_growth": -0.181},
    }
    monkeypatch.setattr(alert_service, "_find_watchlist_target", lambda **_: target)
    monkeypatch.setattr(alert_service, "get_db", lambda: {})
    monkeypatch.setattr(alert_service, "_target_snapshots", lambda *_args, **_kwargs: [snapshot])

    result = alert_service.get_watchlist_target_risk_detail("monitor-risk-1")

    assert result is not None
    assert result["has_snapshot"] is True
    assert result["latest_snapshot"]["score_breakdown"]["财务风险"]["归一化"] == 3.4
    assert result["history"] == [{"snapshot_id": "snapshot-1", "snapshot_version": 1, "checked_at": "2026-09-09T08:00:00+00:00", "risk_score": 7, "risk_level": "低风险"}]


def test_resolve_watchlist_identity_reuses_company_identity_search(monkeypatch):
    target = {
        "monitor_target_id": "monitor-identity-1",
        "company_name": "待确认供应商有限公司",
        "display_name": "待确认供应商有限公司",
    }
    monkeypatch.setattr(alert_service, "_find_watchlist_target", lambda **_: target)
    monkeypatch.setattr(
        "app.domains.company.service.search_identity",
        lambda query, limit: {
            "resolution": "candidates",
            "exact": None,
            "candidates": [{"company_id": "company-1", "legal_name": query, "confidence": 0.91}],
        },
    )

    result = alert_service.resolve_watchlist_identity("monitor-identity-1")

    assert result["monitor_target_id"] == "monitor-identity-1"
    assert result["query"] == "待确认供应商有限公司"
    assert result["candidates"][0]["company_id"] == "company-1"


def test_resolve_watchlist_identity_includes_feishu_supplier_candidate(monkeypatch):
    target = {
        "monitor_target_id": "monitor-feishu-identity-1",
        "company_name": "上海海拉电子有限公司",
        "display_name": "上海海拉电子有限公司",
    }
    monkeypatch.setattr(alert_service, "_find_watchlist_target", lambda **_: target)
    monkeypatch.setattr(
        "app.domains.company.service.search_identity",
        lambda query, limit: {"resolution": "pending_verification", "exact": None, "candidates": []},
    )
    monkeypatch.setattr(
        "app.domains.alert.intake_service._load_local_candidates",
        lambda query: [{
            "candidate_id": "supplier:supplier:feishu:hella",
            "candidate_type": "supplier",
            "supplier_id": "supplier:feishu:hella",
            "supplier_code": "8310242",
            "company_id": None,
            "legal_name": query,
            "unified_social_credit_code": "91310115607341266A",
            "registration_status": "存续",
            "verification_status": "verified",
            "match_type": "legal_name",
            "confidence": 1.0,
            "source": "飞书正式供应商主数据",
        }],
    )

    result = alert_service.resolve_watchlist_identity("monitor-feishu-identity-1")

    assert result["resolution"] == "candidates"
    assert result["candidates"][0]["source"] == "飞书正式供应商主数据"
    assert result["candidates"][0]["supplier_code"] == "8310242"
    assert result["candidates"][0]["verification_status"] == "pending_verification"
    assert "待核验正式企业主体" in result["candidates"][0]["binding_note"]


def test_resolve_watchlist_identity_includes_cached_external_identity(monkeypatch):
    target = {
        "monitor_target_id": "monitor-external-identity-1",
        "company_name": "赛克瑞浦动力电池系统有限公司",
        "display_name": "赛克瑞浦动力电池系统有限公司",
    }
    monkeypatch.setattr(alert_service, "_find_watchlist_target", lambda **_: target)
    monkeypatch.setattr(
        "app.domains.company.service.search_identity",
        lambda query, limit: {"resolution": "pending_verification", "exact": None, "candidates": []},
    )
    monkeypatch.setattr("app.domains.alert.intake_service._load_local_candidates", lambda query: [])
    monkeypatch.setattr(
        "app.domains.alert.intake_service._load_external_profile",
        lambda query: ({
            "company_name": query,
            "registration_number": "450205000188432",
            "registration_status": "存续",
            "legal_person": "廖鸿胡",
            "source_reference": f"tyc:{query}",
        }, {"key": "enterprise", "label": "企业工商与风险", "status": "available", "detail": "已取得天眼查快照"}),
    )

    result = alert_service.resolve_watchlist_identity("monitor-external-identity-1")

    assert result["resolution"] == "candidates"
    assert result["candidates"][0]["candidate_type"] == "external_identity"
    assert result["candidates"][0]["registration_number"] == "450205000188432"
    assert result["candidates"][0]["verification_status"] == "pending_verification"
    assert "才能绑定监控" in result["candidates"][0]["binding_note"]


def test_resolve_monitor_identity_chat_path_includes_external_identity(monkeypatch):
    monkeypatch.setattr(
        "app.domains.company.service.search_identity",
        lambda query, limit: {"resolution": "pending_verification", "exact": None, "candidates": []},
    )
    monkeypatch.setattr(
        "app.domains.alert.intake_service._load_external_profile",
        lambda query: ({
            "company_name": query,
            "unified_social_credit_code": "91450200MAA7L76A5R",
            "registration_number": "450205000188432",
            "registration_status": "存续",
            "legal_person": "廖鸿胡",
            "source_reference": f"tyc:{query}",
        }, {"status": "available"}),
    )

    result = alert_tools.resolve_monitor_identity.func(company_name="赛克瑞浦动力电池系统有限公司")

    assert result["resolution"] == "candidates"
    assert result["candidates"][0]["candidate_type"] == "external_identity"
    assert result["candidates"][0]["unified_social_credit_code"] == "91450200MAA7L76A5R"
    assert result["candidates"][0]["registration_number"] == "450205000188432"
    assert result["candidates"][0]["verification_status"] == "pending_verification"


def test_confirm_watchlist_identity_binds_verified_company_and_audit(monkeypatch):
    target = {
        "monitor_target_id": "monitor-identity-1",
        "company_name": "待确认供应商有限公司",
        "display_name": "待确认供应商有限公司",
        "target_type": "company",
        "identity_status": "unresolved",
    }
    db = {"watchlist": MagicMock()}
    monkeypatch.setattr(alert_service, "_find_watchlist_target", lambda **_: target)
    monkeypatch.setattr(alert_service, "get_db", lambda: db)
    monkeypatch.setattr(alert_service, "_broadcast_alert_update", lambda: None)
    monkeypatch.setattr(
        "app.domains.company.service.get_company",
        lambda company_id: {
            "id": company_id,
            "legal_name": "已核验供应商有限公司",
            "verification_status": "verified",
            "identity_source": "tianyancha",
            "source_reference": "https://example.test/company-1",
        },
    )

    result = alert_service.confirm_watchlist_identity(
        "monitor-identity-1",
        "company-1",
        "user-1",
        source_reference="采购人员确认",
        comment="名称与统一社会信用代码一致",
    )

    update = db["watchlist"].update_one.call_args.args[1]["$set"]
    assert update["company_id"] == "company-1"
    assert update["identity_status"] == "verified"
    assert update["display_name"] == "已核验供应商有限公司"
    assert update["identity_confirmation"]["confirmed_by"] == "user-1"
    assert result["status"] == "verified"


def test_save_snapshot_versions_are_scoped_to_monitor_target(monkeypatch):
    db = {name: MagicMock() for name in ("suppliers", "watchlist", "alert_snapshots")}
    suppliers = db["suppliers"]
    watchlist = db["watchlist"]
    snapshots = db["alert_snapshots"]
    suppliers.find_one.return_value = {
        "_id": "supplier-1",
        "name": "正式供应商有限公司",
        "company_id": "company-1",
    }
    watchlist.find_one.return_value = {
        "monitor_target_id": "monitor-1",
        "target_type": "formal_supplier",
        "supplier_id": "supplier-1",
        "company_id": "company-1",
        "company_name": "正式供应商有限公司",
    }
    snapshots.find_one.return_value = {
        "snapshot_id": "snapshot-3",
        "snapshot_version": 3,
    }
    monkeypatch.setattr(alert_service, "get_db", lambda: db)
    monkeypatch.setattr(
        "app.domains.sourcing.supplier_repo.resolve_supplier_id",
        lambda name, auto_create=False: "supplier-1",
    )

    alert_service.save_snapshot(
        "正式供应商有限公司",
        RiskCalculateResponse(risk_score=42, risk_level="中风险"),
    )

    query = snapshots.find_one.call_args.args[0]
    saved = snapshots.insert_one.call_args.args[0]
    assert query == {"monitor_target_id": "monitor-1"}
    assert saved["monitor_target_id"] == "monitor-1"
    assert saved["snapshot_version"] == 4


def test_alert_change_keeps_monitor_target_identity(monkeypatch):
    db = {name: MagicMock() for name in ("alert_snapshots", "alerts")}
    cursor = db["alert_snapshots"].find.return_value
    cursor.sort.return_value = cursor
    cursor.limit.return_value = [
        {
            "company_name": "正式供应商有限公司",
            "monitor_target_id": "monitor-1",
            "target_type": "formal_supplier",
            "supplier_id": "supplier-1",
            "company_id": "company-1",
            "risk_score": 70,
            "risk_level": "高风险",
            "risk_detail": {},
            "checked_at": None,
        },
        {
            "company_name": "正式供应商有限公司",
            "monitor_target_id": "monitor-1",
            "risk_score": 40,
            "risk_level": "低风险",
            "risk_detail": {},
            "checked_at": None,
        },
    ]
    monkeypatch.setattr(alert_service, "get_db", lambda: db)
    monkeypatch.setattr("app.domains.alert.rules.get_rules", lambda name: [])
    monkeypatch.setattr(
        "app.domains.alert.rules.evaluate_changes",
        lambda rules, changes: [{**change, "severity": "warning"} for change in changes],
    )
    monkeypatch.setattr("app.domains.sourcing.supplier_repo.resolve_supplier_id", lambda name: "supplier-1")
    monkeypatch.setattr("app.services.feishu.send_alert_card", lambda *args, **kwargs: None)

    result = alert_service.detect_changes(
        "正式供应商有限公司",
        monitor_target_id="monitor-1",
    )

    assert result["monitor_target_id"] == "monitor-1"
    assert db["alerts"].insert_one.call_args.args[0]["monitor_target_id"] == "monitor-1"


def test_risk_change_does_not_treat_missing_history_as_stable():
    assert alert_service._risk_change([]) == {
        "status": "no_data",
        "label": "暂无快照",
        "delta": None,
        "previous_score": None,
    }
    assert alert_service._risk_change([{"risk_score": 22}])["status"] == "insufficient_data"


def test_monitor_target_summary_exposes_procurement_next_action(monkeypatch):
    target = {
        "monitor_target_id": "monitor-candidate-1",
        "company_name": "外部候选有限公司",
        "display_name": "外部候选有限公司",
        "target_type": "external_candidate",
        "identity_status": "candidate",
    }
    monkeypatch.setattr(alert_service, "get_db", lambda: object())
    monkeypatch.setattr(alert_service, "get_watchlist_targets", lambda: [target])
    monkeypatch.setattr(alert_service, "_target_snapshots", lambda *_args, **_kwargs: [])

    summaries = alert_service.get_watchlist_target_summaries()

    assert summaries[0]["risk_change"]["status"] == "no_data"
    assert summaries[0]["next_action"]["code"] == "verify_identity"


def test_supervisor_risk_evidence_carries_monitor_target_id(monkeypatch):
    assessment = SimpleNamespace(
        risk_score=18,
        risk_level="低风险",
        risk_detail={"data_coverage": {"assessment_status": "complete", "coverage_ratio": 1}},
        model_dump=lambda: {
            "risk_score": 18,
            "risk_level": "低风险",
            "risk_detail": {"data_coverage": {"assessment_status": "complete", "coverage_ratio": 1}},
        },
    )
    monkeypatch.setattr(
        "app.domains.risk.service.calculate_company_risk_preview",
        lambda company_name: assessment,
    )
    context = AgentTaskContext(
        task=PlannerTask(task_id="risk", agent="risk"),
        run_id="run-1",
        user_query="分析正式供应商有限公司",
        intent={"company_name": "正式供应商有限公司"},
        dependency_results={},
        supplier_references=[{
            "name": "正式供应商有限公司",
            "monitor_target_id": "monitor-1",
            "target_type": "formal_supplier",
        }],
    )

    result = asyncio.run(_run_risk(context))

    assert result.evidence[0].monitor_target_id == "monitor-1"
    assert result.evidence[0].target_type == "formal_supplier"
