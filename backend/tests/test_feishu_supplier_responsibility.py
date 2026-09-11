from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.services import feishu_supplier_responsibility as responsibility
from app.services.feishu_supplier_responsibility import normalize_responsibility_record


def _record(fields: dict, record_id: str = "rec-responsibility-1") -> dict:
    return {"record_id": record_id, "fields": fields}


def test_normalize_responsibility_record_maps_one_active_purchaser_and_manager() -> None:
    result = normalize_responsibility_record(
        _record({
            "供应商代码": "SUP-001",
            "供应商名称": "示例供应商",
            "是否有效": "是",
            "科室名称": "底盘科",
            "科室编码": "CHASSIS",
            "采购员 (人员 )": [{"id": "ou-buyer", "name": "采购员", "email": "buyer@example.com"}],
            "采购经理": [{"id": "ou-manager", "name": "经理", "email": "manager@example.com"}],
            "生效日期": "2026-09-10",
        }),
        supplier_ids_by_code={"SUP-001": "supplier:feishu:1"},
        synced_at=datetime.now(timezone.utc),
    )

    assert result["sync_status"] == "current"
    assert result["supplier_id"] == "supplier:feishu:1"
    assert result["purchaser_open_id"] == "ou-buyer"
    assert result["manager_open_id"] == "ou-manager"


def test_normalize_responsibility_record_rejects_multiple_active_purchasers() -> None:
    result = normalize_responsibility_record(
        _record({
            "供应商代码": "SUP-001",
            "供应商名称": "示例供应商",
            "是否有效": "是",
            "科室名称": "底盘科",
            "科室编码": "CHASSIS",
            "采购员 (人员 )": [{"id": "ou-a"}, {"id": "ou-b"}],
            "采购经理": [{"id": "ou-manager"}],
        }),
        supplier_ids_by_code={"SUP-001": "supplier:feishu:1"},
        synced_at=datetime.now(timezone.utc),
    )

    assert result["sync_status"] == "invalid"
    assert "有效供应商必须且只能配置一位采购员" in result["validation_errors"]


def test_normalize_responsibility_record_rejects_supplier_not_in_current_master() -> None:
    result = normalize_responsibility_record(
        _record({
            "供应商代码": "SUP-OUTSIDE",
            "供应商名称": "过期供应商",
            "是否有效": "是",
            "科室名称": "底盘科",
            "科室编码": "CHASSIS",
            "采购员 (人员 )": [{"id": "ou-buyer"}],
            "采购经理": [{"id": "ou-manager"}],
        }),
        supplier_ids_by_code={"SUP-001": "supplier:feishu:1"},
        synced_at=datetime.now(timezone.utc),
    )

    assert result["sync_status"] == "invalid"
    assert "供应商不在当前飞书主数据范围内" in result["validation_errors"]


def test_sync_monitor_targets_auto_enrols_and_marks_responsible_supplier(monkeypatch) -> None:
    collection = MagicMock()
    collection.find_one.side_effect = [None, None]
    monkeypatch.setattr(responsibility, "get_db", lambda: {"watchlist": collection})
    monkeypatch.setattr(
        responsibility,
        "_application_user_ids_by_identity",
        lambda: {"ou-buyer": "user-buyer"},
    )
    record = {
        "supplier_id": "supplier:1",
        "supplier_code": "SUP-001",
        "supplier_name": "示例供应商",
        "source_active": True,
        "sync_status": "current",
        "purchaser_open_id": "ou-buyer",
        "purchaser_name": "采购员",
        "purchaser_email": "buyer@example.com",
        "department_code": "CHASSIS",
        "department_name": "底盘科",
        "manager_open_id": "ou-manager",
        "manager_name": "经理",
        "manager_email": "manager@example.com",
        "synced_at": datetime.now(timezone.utc),
    }

    result = responsibility._sync_monitor_targets([record])

    assert result == {"monitored": 1, "errors": []}
    update_filter, update_doc = collection.update_one.call_args_list[0].args[:2]
    assert update_filter == {"supplier_id": "supplier:1"}
    assert update_doc["$set"]["target_type"] == "formal_supplier"
    assert update_doc["$set"]["is_responsible_supplier"] is True
    assert update_doc["$set"]["responsibility_status"] == "assigned"
    assert update_doc["$set"]["owner_user_id"] == "user-buyer"
