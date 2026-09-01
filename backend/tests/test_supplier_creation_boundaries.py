from unittest.mock import MagicMock
from datetime import datetime, timezone

from app.domains.alert import service as alert_service
from app.schemas import RiskCalculateResponse
from app.services import tianyancha_client


def _capture_supplier_resolution(monkeypatch, supplier_id: str | None = None):
    auto_create_values: list[bool] = []

    def fake_resolve_supplier_id(
        name: str,
        auto_create: bool = False,
    ) -> str | None:
        auto_create_values.append(auto_create)
        return supplier_id

    monkeypatch.setattr(
        "app.domains.sourcing.supplier_repo.resolve_supplier_id",
        fake_resolve_supplier_id,
    )
    return auto_create_values


def test_save_snapshot_does_not_auto_create_supplier(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(alert_service, "get_db", lambda: db)
    auto_create_values = _capture_supplier_resolution(monkeypatch)

    alert_service.save_snapshot(
        "非供应商企业有限公司",
        RiskCalculateResponse(risk_score=20, risk_level="低风险"),
    )

    assert auto_create_values == [False]
    saved = db["alert_snapshots"].insert_one.call_args.args[0]
    assert saved["supplier_id"] is None


def test_save_snapshot_links_existing_supplier(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(alert_service, "get_db", lambda: db)
    auto_create_values = _capture_supplier_resolution(
        monkeypatch,
        supplier_id="supplier-123",
    )

    alert_service.save_snapshot(
        "已有供应商有限公司",
        RiskCalculateResponse(risk_score=20, risk_level="低风险"),
    )

    assert auto_create_values == [False]
    saved = db["alert_snapshots"].insert_one.call_args.args[0]
    assert saved["supplier_id"] == "supplier-123"


def test_save_snapshot_appends_a_version_linked_to_previous_snapshot(monkeypatch):
    db = MagicMock()
    db["alert_snapshots"].find_one.return_value = {
        "snapshot_id": "snapshot-3",
        "snapshot_version": 3,
    }
    monkeypatch.setattr(alert_service, "get_db", lambda: db)
    _capture_supplier_resolution(monkeypatch, supplier_id="supplier-123")

    alert_service.save_snapshot(
        "已有供应商有限公司",
        RiskCalculateResponse(risk_score=42, risk_level="中风险"),
    )

    saved = db["alert_snapshots"].insert_one.call_args.args[0]
    assert saved["snapshot_id"]
    assert saved["snapshot_version"] == 4
    assert saved["previous_snapshot_id"] == "snapshot-3"
    assert saved["scoring_version"] == "v2"


def test_snapshot_history_limits_and_orders_by_version(monkeypatch):
    db = MagicMock()
    cursor = db["alert_snapshots"].find.return_value
    cursor.sort.return_value = cursor
    cursor.limit.return_value = [{"snapshot_id": "snapshot-1", "snapshot_version": 1}]
    monkeypatch.setattr(alert_service, "get_db", lambda: db)

    result = alert_service.get_snapshot_history("供应商有限公司", limit=1000)

    assert result == [{"snapshot_id": "snapshot-1", "snapshot_version": 1}]
    db["alert_snapshots"].find.assert_called_once_with({"company_name": "供应商有限公司"})
    cursor.sort.assert_called_once_with([("snapshot_version", -1), ("checked_at", -1)])
    cursor.limit.assert_called_once_with(100)


def test_add_to_watchlist_does_not_auto_create_supplier(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(alert_service, "get_db", lambda: db)
    monkeypatch.setattr(alert_service, "_broadcast_alert_update", lambda: None)
    auto_create_values = _capture_supplier_resolution(monkeypatch)

    result = alert_service.add_to_watchlist("监控企业有限公司")

    assert auto_create_values == [False]
    assert result["supplier_id"] is None
    update = db["watchlist"].update_one.call_args.args[1]["$set"]
    assert update["supplier_id"] is None


def test_tianyancha_save_does_not_auto_create_supplier(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(tianyancha_client, "get_db", lambda: db)
    auto_create_values = _capture_supplier_resolution(monkeypatch)

    tianyancha_client._save("baseinfo", "查询企业有限公司", {"ok": True}, "items")

    assert auto_create_values == [False]
    update = db["baseinfo"].update_one.call_args.args[1]["$set"]
    assert update["supplier_id"] is None
