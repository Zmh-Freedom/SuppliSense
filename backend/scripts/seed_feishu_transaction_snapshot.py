"""Seed explicit synthetic transaction snapshots into a development Feishu Bitable table.

This script is intentionally outside the application service layer. Runtime services
remain read-only; writes require the explicit ``--confirm-test-write`` flag.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.feishu_bitable import (
    FeishuBitableClient,
    FeishuBitableError,
    build_supplier_master_client,
    build_supplier_transaction_client,
)

REQUIRED_TRANSACTION_FIELDS = frozenset({
    "快照ID",
    "统计月份",
    "快照日期",
    "供应商代码",
    "供应商名称",
    "采购组织代码",
    "基地",
    "物料号",
    "物料名称",
    "计量单位",
    "收货数量",
    "正结算数量",
    "负结算数量",
    "实结算数量",
    "已结算数量",
    "未结算数量",
    "单价",
    "币种",
    "金额口径",
    "合同状态",
    "收货金额",
    "实结算金额",
    "已结算金额",
    "未结算金额",
    "数据来源",
    "数据模式",
    "更新时间",
})
SEED_PREFIX = "SYN-TXN-V1"


def _text_value(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            result = _text_value(item)
            if result:
                return result
        return None
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            result = _text_value(value.get(key))
            if result:
                return result
        return None
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _date_value(value: date | datetime, field_type: int | None) -> int | str:
    if isinstance(value, datetime):
        normalized = value.astimezone(timezone.utc)
    else:
        normalized = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if field_type == 5:
        return int(normalized.timestamp() * 1000)
    return normalized.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime) else normalized.strftime("%Y-%m-%d")


def _value_for_field_type(value: Any, field_type: int | None) -> Any:
    """Serialize values for the configured Bitable field type.

    Empty-template imports often create text columns. Keeping a synthetic seed
    compatible with that temporary schema is safe because every created row is
    explicitly marked ``data_mode=synthetic``.
    """
    if field_type != 1 or not isinstance(value, (int, float)):
        return value
    if isinstance(value, float):
        return format(value, ".2f")
    return str(value)


def _request(client: FeishuBitableClient, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = httpx.request(
            method,
            f"{client.base_url}{path}",
            timeout=client.timeout_seconds,
            **kwargs,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise FeishuBitableError(f"飞书测试写入接口请求失败: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("code", 0) != 0:
        message = payload.get("msg", "") if isinstance(payload, dict) else "响应格式无效"
        raise FeishuBitableError(f"飞书测试写入接口返回错误: {message}")
    return payload


def get_field_types(client: FeishuBitableClient) -> dict[str, int | None]:
    token = client.get_tenant_access_token()
    payload = _request(
        client,
        "GET",
        f"/open-apis/bitable/v1/apps/{client.app_token}/tables/{client.table_id}/fields",
        params={"page_size": 100},
        headers={"Authorization": f"Bearer {token}"},
    )
    data = payload.get("data")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise FeishuBitableError("飞书交易表字段响应缺少 items")

    return {
        name: field.get("type") if isinstance(field.get("type"), int) else None
        for field in items
        if isinstance(field, dict)
        for name in [_text_value(field.get("field_name"))]
        if name
    }


def get_seed_suppliers(client: FeishuBitableClient) -> list[dict[str, str]]:
    suppliers: list[dict[str, str]] = []
    for record in client.list_records():
        fields = record.get("fields")
        if not isinstance(fields, dict):
            continue
        supplier_code = _text_value(fields.get("供应商代码"))
        supplier_name = _text_value(fields.get("供应商名称"))
        if supplier_code and supplier_name:
            suppliers.append({"supplier_code": supplier_code, "supplier_name": supplier_name})
        if len(suppliers) == 3:
            break

    return suppliers or [
        {"supplier_code": "TEST-SUP-001", "supplier_name": "测试供应商一号"},
        {"supplier_code": "TEST-SUP-002", "supplier_name": "测试供应商二号"},
        {"supplier_code": "TEST-SUP-003", "supplier_name": "测试供应商三号"},
    ]


def build_synthetic_records(
    suppliers: list[dict[str, str]],
    field_types: dict[str, int | None],
) -> list[dict[str, Any]]:
    if not suppliers:
        raise ValueError("至少需要一个供应商用于生成测试数据")

    normalized_suppliers = (suppliers * 3)[:3]
    months = [date(2026, month, 1) for month in range(5, 9)]
    snapshot_at = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    scenarios = [
        {"material": "TEST-CAM-001", "material_name": "测试摄像头模组", "quantity": 8000, "price": 120.0, "unsettled_rate": 0.25, "contract_status": "即将到期", "negative": 0},
        {"material": "TEST-CAM-002", "material_name": "测试摄像头模组备用件", "quantity": 2200, "price": 121.5, "unsettled_rate": 0.05, "contract_status": "有效", "negative": 0},
        {"material": "TEST-CAM-003", "material_name": "测试摄像头模组替代件", "quantity": 1800, "price": 124.0, "unsettled_rate": 0.10, "contract_status": "有效", "negative": 100},
    ]
    records: list[dict[str, Any]] = []

    for month_index, month in enumerate(months):
        for supplier, scenario in zip(normalized_suppliers, scenarios, strict=True):
            positive_quantity = scenario["quantity"] + month_index * 100
            negative_quantity = -scenario["negative"] if month_index == 2 else 0
            settled_quantity = positive_quantity + negative_quantity
            unit_price = scenario["price"] + month_index * 0.5
            received_amount = round(positive_quantity * unit_price, 2)
            settled_amount = round(settled_quantity * unit_price, 2)
            unsettled_amount = round(settled_amount * scenario["unsettled_rate"], 2)
            paid_amount = round(settled_amount - unsettled_amount, 2)
            unsettled_quantity = round(settled_quantity * scenario["unsettled_rate"], 2)
            paid_quantity = round(settled_quantity - unsettled_quantity, 2)
            snapshot_id = (
                f"{SEED_PREFIX}-{month.strftime('%Y%m')}-{supplier['supplier_code']}-"
                f"{scenario['material']}"
            )
            fields = {
                "快照ID": snapshot_id,
                "统计月份": _date_value(month, field_types.get("统计月份")),
                "快照日期": _date_value(month, field_types.get("快照日期")),
                "供应商代码": supplier["supplier_code"],
                "供应商名称": supplier["supplier_name"],
                "采购组织代码": "TEST-PO01",
                "基地": "测试基地",
                "品类代码": "TEST_CAMERA_MODULE",
                "品类名称": "测试摄像头模组",
                "物料号": scenario["material"],
                "物料名称": scenario["material_name"],
                "计量单位": "件",
                "收货数量": positive_quantity,
                "正结算数量": positive_quantity,
                "负结算数量": negative_quantity,
                "实结算数量": settled_quantity,
                "已结算数量": paid_quantity,
                "未结算数量": unsettled_quantity,
                "单价": unit_price,
                "币种": "CNY",
                "金额口径": "不含税",
                "合同编号": f"TEST-HT-{supplier['supplier_code']}",
                "合同状态": scenario["contract_status"],
                "收货金额": received_amount,
                "实结算金额": settled_amount,
                "已结算金额": paid_amount,
                "未结算金额": unsettled_amount,
                "数据来源": "手工导入",
                "数据模式": "synthetic",
                "更新时间": _date_value(snapshot_at, field_types.get("更新时间")),
            }
            records.append({
                field_name: _value_for_field_type(value, field_types.get(field_name))
                for field_name, value in fields.items()
            })
    return records


def create_records(client: FeishuBitableClient, records: list[dict[str, Any]]) -> list[str]:
    if not records:
        return []
    token = client.get_tenant_access_token()
    payload = _request(
        client,
        "POST",
        f"/open-apis/bitable/v1/apps/{client.app_token}/tables/{client.table_id}/records/batch_create",
        headers={"Authorization": f"Bearer {token}"},
        json={"records": [{"fields": record} for record in records]},
    )
    data = payload.get("data")
    created = data.get("records") if isinstance(data, dict) else None
    if not isinstance(created, list):
        raise FeishuBitableError("飞书测试写入响应缺少 records")
    return [
        record_id
        for item in created
        if isinstance(item, dict)
        for record_id in [_text_value(item.get("record_id"))]
        if record_id
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="向飞书供应商交易月度快照表写入合成测试数据")
    parser.add_argument(
        "--confirm-test-write",
        action="store_true",
        help="确认向配置的飞书交易表写入 synthetic 测试数据",
    )
    args = parser.parse_args()
    if not args.confirm_test_write:
        parser.error("写入前必须显式传入 --confirm-test-write")

    transaction_client = build_supplier_transaction_client()
    field_types = get_field_types(transaction_client)
    missing_fields = sorted(REQUIRED_TRANSACTION_FIELDS - field_types.keys())
    if missing_fields:
        raise FeishuBitableError(f"飞书交易表缺少模板字段: {', '.join(missing_fields)}")

    existing_ids = {
        _text_value(record.get("fields", {}).get("快照ID"))
        for record in transaction_client.list_records()
        if isinstance(record.get("fields"), dict)
    }
    suppliers = get_seed_suppliers(build_supplier_master_client())
    planned_records = build_synthetic_records(suppliers, field_types)
    records_to_create = [
        record for record in planned_records if record["快照ID"] not in existing_ids
    ]
    created_ids = create_records(transaction_client, records_to_create)
    print(json.dumps({
        "status": "ok",
        "planned": len(planned_records),
        "created": len(created_ids),
        "skipped_existing": len(planned_records) - len(records_to_create),
        "record_ids": created_ids,
        "data_mode": "synthetic",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
