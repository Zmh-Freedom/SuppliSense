"""Read-only synchronization of supplier responsibility assignments from Feishu.

The Feishu table is the business source for who owns a formal supplier.  This
module persists a validated local snapshot only; it deliberately does not
create application users or grant permissions as a side effect.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.db.mongo import get_db
from app.db.postgres import get_cursor
from app.services.feishu_bitable import (
    FeishuBitableClient,
    build_supplier_assignment_client,
)

logger = get_logger()

_REQUIRED_FIELDS = (
    "供应商代码",
    "供应商名称",
    "是否有效",
    "科室名称",
    "科室编码",
    "采购员 (人员 )",
    "采购经理",
)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(part for item in value if (part := _text(item)))
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if value.get(key) is not None:
                return _text(value[key])
    return ""


def _people(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    people: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        people.append({
            "open_id": _text(item.get("id") or item.get("open_id")),
            "name": _text(item.get("name")),
            "email": _text(item.get("email")),
        })
    return people


def _source_active(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).casefold() in {"是", "有效", "true", "1", "active"}


def normalize_responsibility_record(
    record: dict[str, Any],
    *,
    supplier_ids_by_code: dict[str, str],
    synced_at: datetime,
) -> dict[str, Any]:
    """Validate one Feishu responsibility record against the formal directory."""
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    record_id = _text(record.get("record_id"))
    supplier_code = _text(fields.get("供应商代码"))
    supplier_name = _text(fields.get("供应商名称"))
    active = _source_active(fields.get("是否有效"))
    purchaser_people = _people(fields.get("采购员 (人员 )"))
    manager_people = _people(fields.get("采购经理"))
    errors: list[str] = []

    if not record_id:
        errors.append("缺少飞书记录 ID")
    if not supplier_code:
        errors.append("缺少供应商代码")
    if not supplier_name:
        errors.append("缺少供应商名称")
    if active:
        if not _text(fields.get("科室名称")):
            errors.append("缺少科室名称")
        if not _text(fields.get("科室编码")):
            errors.append("缺少科室编码")
        if len(purchaser_people) != 1 or not purchaser_people[0].get("open_id"):
            errors.append("有效供应商必须且只能配置一位采购员")
        if len(manager_people) != 1 or not manager_people[0].get("open_id"):
            errors.append("有效供应商必须且只能配置一位采购经理")
        if supplier_code and supplier_code not in supplier_ids_by_code:
            errors.append("供应商不在当前飞书主数据范围内")

    purchaser = purchaser_people[0] if len(purchaser_people) == 1 else {}
    manager = manager_people[0] if len(manager_people) == 1 else {}
    return {
        "source_record_id": record_id,
        "supplier_code": supplier_code,
        "supplier_id": supplier_ids_by_code.get(supplier_code),
        "supplier_name": supplier_name,
        "source_active": active,
        "department_code": _text(fields.get("科室编码")),
        "department_name": _text(fields.get("科室名称")),
        "purchaser_open_id": purchaser.get("open_id"),
        "purchaser_name": purchaser.get("name"),
        "purchaser_email": purchaser.get("email"),
        "manager_open_id": manager.get("open_id"),
        "manager_name": manager.get("name"),
        "manager_email": manager.get("email"),
        "effective_date": _text(fields.get("生效日期")),
        "validation_errors": errors,
        "sync_status": "invalid" if errors else "current",
        "synced_at": synced_at,
    }


def _current_supplier_ids_by_code() -> dict[str, str]:
    """Return the current Feishu formal supplier directory keyed by code."""
    collection = get_db()["supplier_master_snapshots"]
    rows = collection.find({"source": "feishu_bitable", "sync_status": "current"})
    return {
        _text(row.get("supplier_code")): _text(row.get("supplier_id") or row.get("_id"))
        for row in rows
        if _text(row.get("supplier_code")) and _text(row.get("supplier_id") or row.get("_id"))
    }


def _mark_duplicate_active_codes(records: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for record in records:
        if record["source_active"] and record["supplier_code"]:
            code = record["supplier_code"]
            counts[code] = counts.get(code, 0) + 1
    for record in records:
        if record["source_active"] and counts.get(record["supplier_code"], 0) > 1:
            record["validation_errors"].append("同一供应商代码存在多条有效责任分配")
            record["sync_status"] = "invalid"


def _persist(records: list[dict[str, Any]], batch_id: str, synced_at: datetime) -> None:
    with get_cursor() as (_, cursor):
        for record in records:
            cursor.execute(
                """
                INSERT INTO supplier_responsibility_snapshots (
                    source_record_id, supplier_code, supplier_id, supplier_name,
                    source_active, department_code, department_name,
                    purchaser_open_id, purchaser_name, purchaser_email,
                    manager_open_id, manager_name, manager_email, effective_date,
                    validation_errors, sync_status, sync_batch_id, synced_at, updated_at
                ) VALUES (
                    %(source_record_id)s, %(supplier_code)s, %(supplier_id)s, %(supplier_name)s,
                    %(source_active)s, %(department_code)s, %(department_name)s,
                    %(purchaser_open_id)s, %(purchaser_name)s, %(purchaser_email)s,
                    %(manager_open_id)s, %(manager_name)s, %(manager_email)s, %(effective_date)s,
                    %(validation_errors)s::jsonb, %(sync_status)s, %(sync_batch_id)s::uuid,
                    %(synced_at)s, NOW()
                )
                ON CONFLICT (source_record_id) DO UPDATE SET
                    supplier_code = EXCLUDED.supplier_code,
                    supplier_id = EXCLUDED.supplier_id,
                    supplier_name = EXCLUDED.supplier_name,
                    source_active = EXCLUDED.source_active,
                    department_code = EXCLUDED.department_code,
                    department_name = EXCLUDED.department_name,
                    purchaser_open_id = EXCLUDED.purchaser_open_id,
                    purchaser_name = EXCLUDED.purchaser_name,
                    purchaser_email = EXCLUDED.purchaser_email,
                    manager_open_id = EXCLUDED.manager_open_id,
                    manager_name = EXCLUDED.manager_name,
                    manager_email = EXCLUDED.manager_email,
                    effective_date = EXCLUDED.effective_date,
                    validation_errors = EXCLUDED.validation_errors,
                    sync_status = EXCLUDED.sync_status,
                    sync_batch_id = EXCLUDED.sync_batch_id,
                    synced_at = EXCLUDED.synced_at,
                    updated_at = NOW()
                """,
                {**record, "validation_errors": json.dumps(record["validation_errors"]), "sync_batch_id": batch_id},
            )
        cursor.execute(
            """
            UPDATE supplier_responsibility_snapshots
            SET sync_status = 'stale', updated_at = NOW()
            WHERE sync_batch_id <> %s::uuid AND sync_status <> 'stale'
            """,
            (batch_id,),
        )


def sync_supplier_responsibilities(
    client: FeishuBitableClient | None = None,
) -> dict[str, Any]:
    """Sync Feishu ownership rows after formal supplier master data is current."""
    if not settings.FEISHU_BITABLE_ENABLED:
        return {"enabled": False, "status": "disabled", "synced": 0, "invalid": 0}
    if not settings.FEISHU_SUPPLIER_ASSIGNMENT_TABLE_ID:
        return {"enabled": False, "status": "not_configured", "synced": 0, "invalid": 0}

    supplier_ids = _current_supplier_ids_by_code()
    if not supplier_ids:
        return {
            "enabled": True,
            "status": "blocked",
            "synced": 0,
            "invalid": 0,
            "errors": ["当前飞书供应商主数据快照为空，请先同步主数据"],
        }

    synced_at = datetime.now(timezone.utc)
    batch_id = str(uuid.uuid4())
    raw_records = (client or build_supplier_assignment_client()).list_records()
    records = [
        normalize_responsibility_record(
            record, supplier_ids_by_code=supplier_ids, synced_at=synced_at
        )
        for record in raw_records
    ]
    _mark_duplicate_active_codes(records)
    _persist(records, batch_id, synced_at)
    invalid = [record for record in records if record["sync_status"] == "invalid"]
    current = [record for record in records if record["sync_status"] == "current"]
    logger.info(
        "feishu_supplier_responsibilities_synced",
        synced=len(current), invalid=len(invalid), batch_id=batch_id,
    )
    return {
        "enabled": True,
        "status": "ok" if not invalid else "partial_invalid",
        "synced": len(current),
        "inactive": sum(1 for record in current if not record["source_active"]),
        "invalid": len(invalid),
        "batch_id": batch_id,
        "synced_at": synced_at.isoformat(),
        "errors": [
            {"supplier_code": record["supplier_code"], "reasons": record["validation_errors"]}
            for record in invalid
        ],
    }
