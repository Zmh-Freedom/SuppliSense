from __future__ import annotations

from datetime import datetime, timezone

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
