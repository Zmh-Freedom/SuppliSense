from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domains.alert import service as alert_service
from app.graphs.agent_supervisor.agents import AgentTaskContext, _run_risk
from app.graphs.agent_supervisor.contracts import PlannerTask
from app.schemas import RiskCalculateResponse


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


@pytest.mark.asyncio
async def test_supervisor_risk_evidence_carries_monitor_target_id(monkeypatch):
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

    result = await _run_risk(context)

    assert result.evidence[0].monitor_target_id == "monitor-1"
    assert result.evidence[0].target_type == "formal_supplier"
