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


def _link_application_users() -> int:
    """Link application accounts to Feishu people by the verified email field."""
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            UPDATE users AS account
            SET feishu_open_id = identity.open_id, updated_at = NOW()
            FROM (
                SELECT DISTINCT ON (LOWER(email)) LOWER(email) AS email, open_id
                FROM (
                    SELECT purchaser_email AS email, purchaser_open_id AS open_id
                    FROM supplier_responsibility_snapshots
                    WHERE source_active = TRUE AND sync_status = 'current'
                    UNION ALL
                    SELECT manager_email AS email, manager_open_id AS open_id
                    FROM supplier_responsibility_snapshots
                    WHERE source_active = TRUE AND sync_status = 'current'
                ) people
                WHERE email IS NOT NULL AND BTRIM(email) <> ''
                  AND open_id IS NOT NULL AND BTRIM(open_id) <> ''
                ORDER BY LOWER(email), open_id
            ) AS identity
            WHERE LOWER(account.email) = identity.email
              AND account.feishu_open_id IS DISTINCT FROM identity.open_id
            """
        )
        return cursor.rowcount


def _application_user_ids_by_identity() -> dict[str, str]:
    """Return active application user IDs keyed by email and Feishu open_id."""
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT id, email, feishu_open_id
            FROM users
            WHERE is_active = TRUE
            """
        )
        identities: dict[str, str] = {}
        for user_id, email, open_id in cursor.fetchall():
            user_id_text = str(user_id)
            for value in (email, open_id):
                normalized = _text(value).casefold()
                if normalized:
                    identities[normalized] = user_id_text
        return identities


def _sync_monitor_targets(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Auto-enrol current formal suppliers into monitoring with ownership metadata."""
    db = get_db()
    collection = db["watchlist"]
    try:
        user_ids = _application_user_ids_by_identity()
    except Exception as exc:
        logger.warning("feishu_monitor_user_lookup_failed", error=str(exc))
        user_ids = {}

    active_records = [
        record for record in records
        if record.get("source_active")
        and record.get("sync_status") == "current"
        and record.get("supplier_id")
    ]
    active_supplier_ids = {str(record["supplier_id"]) for record in active_records}
    monitored = 0
    assessed = 0
    errors: list[dict[str, str]] = []
    assessment_errors: list[dict[str, str]] = []
    for record in active_records:
        supplier_id = str(record["supplier_id"])
        company_name = _text(record.get("supplier_name"))
        try:
            existing = collection.find_one({"supplier_id": supplier_id})
            if existing is None and company_name:
                # Reuse a legacy name-keyed row instead of creating a duplicate
                # when the supplier identity is first linked from Feishu.
                existing = collection.find_one({"company_name": company_name})
            target_id = str((existing or {}).get("monitor_target_id") or uuid.uuid4())
            current_status = str((existing or {}).get("monitor_status") or "active")
            owner_user_id = user_ids.get(
                _text(record.get("purchaser_open_id")).casefold()
            ) or user_ids.get(_text(record.get("purchaser_email")).casefold())
            target_doc = {
                "monitor_target_id": target_id,
                "target_type": "formal_supplier",
                "identity_status": "verified",
                "company_name": company_name,
                "display_name": company_name,
                "supplier_id": supplier_id,
                "supplier_code": _text(record.get("supplier_code")),
                "monitor_status": current_status,
                "is_responsible_supplier": True,
                "responsibility_status": "assigned",
                "responsibility_source": "feishu_responsibility",
                "purchaser_open_id": _text(record.get("purchaser_open_id")) or None,
                "purchaser_name": _text(record.get("purchaser_name")) or None,
                "purchaser_email": _text(record.get("purchaser_email")) or None,
                "department_code": _text(record.get("department_code")) or None,
                "department_name": _text(record.get("department_name")) or None,
                "manager_open_id": _text(record.get("manager_open_id")) or None,
                "manager_name": _text(record.get("manager_name")) or None,
                "manager_email": _text(record.get("manager_email")) or None,
                "owner_user_id": owner_user_id,
                "responsibility_synced_at": record.get("synced_at"),
            }
            identity_filter = {"_id": existing["_id"]} if existing and existing.get("_id") is not None else {"supplier_id": supplier_id}
            collection.update_one(
                identity_filter,
                {"$set": target_doc, "$setOnInsert": {"added_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
            monitored += 1

            # The first enrollment must have a usable risk baseline immediately.
            # Existing snapshots are left untouched so the six-hour Feishu sync
            # remains idempotent and does not repeatedly call paid providers.
            snapshot_exists = _monitor_target_has_snapshot(
                db, monitor_target_id=target_id, company_name=company_name
            )
            if not snapshot_exists:
                target_filter = (
                    {"_id": existing["_id"]}
                    if existing and existing.get("_id") is not None
                    else {"supplier_id": supplier_id}
                )
                collection.update_one(
                    target_filter,
                    {"$set": {
                        "initial_assessment_status": "running",
                        "initial_assessment_started_at": datetime.now(timezone.utc),
                    }},
                )
                try:
                    from app.domains.risk.service import assess_risk
                    from app.schemas import RiskAssessRequest

                    assess_risk(RiskAssessRequest(company_name=company_name))
                    collection.update_one(
                        target_filter,
                        {"$set": {
                            "initial_assessment_status": "completed",
                            "initial_assessment_at": datetime.now(timezone.utc),
                        }, "$unset": {"initial_assessment_error": ""}},
                    )
                    assessed += 1
                except Exception as exc:
                    message = str(exc)[:500]
                    collection.update_one(
                        target_filter,
                        {"$set": {
                            "initial_assessment_status": "failed",
                            "initial_assessment_error": message,
                            "initial_assessment_failed_at": datetime.now(timezone.utc),
                        }},
                    )
                    assessment_errors.append({
                        "supplier_id": supplier_id,
                        "error": message,
                    })
        except Exception as exc:
            logger.warning(
                "feishu_monitor_target_sync_failed",
                supplier_id=supplier_id,
                error=str(exc),
            )
            errors.append({"supplier_id": supplier_id, "error": str(exc)})

    # A supplier that is no longer assigned remains available for historical
    # review, but is no longer labelled as the current user's responsibility.
    try:
        collection.update_many(
            {
                "responsibility_source": "feishu_responsibility",
                "supplier_id": {"$nin": list(active_supplier_ids)},
            },
            {"$set": {"is_responsible_supplier": False, "responsibility_status": "unassigned"}},
        )
    except Exception as exc:
        logger.warning("feishu_monitor_responsibility_cleanup_failed", error=str(exc))
        errors.append({"supplier_id": "*", "error": str(exc)})
    return {
        "monitored": monitored,
        "assessed": assessed,
        "errors": errors,
        "assessment_errors": assessment_errors,
    }


def _monitor_target_has_snapshot(
    db: object,
    *,
    monitor_target_id: str,
    company_name: str,
) -> bool:
    """Check for an existing risk baseline without mixing monitor identities."""
    try:
        snapshots = db["alert_snapshots"]
        if snapshots.find_one({"monitor_target_id": monitor_target_id}):
            return True
        # Compatibility for snapshots written before monitor_target_id existed.
        return bool(company_name and snapshots.find_one({"company_name": company_name}))
    except Exception:
        # A missing snapshot collection should be treated as no baseline; the
        # assessment attempt will surface the actual persistence/config error.
        return False


def backfill_initial_assessments(*, force: bool = False) -> dict[str, Any]:
    """建立当前监控对象的首次风险基线，并回填可追溯的状态。

    这是管理员触发的一次性运维动作，不读取或写入飞书责任分配表。已有
    风险快照的对象只补记为已完成，避免重复调用外部风险数据服务；没有快照
    的对象才执行一次完整评估，并把成功或失败写回监控对象。
    """
    db = get_db()
    collection = db["watchlist"]
    targets = list(collection.find({"monitor_status": {"$ne": "removed"}}))
    result: dict[str, Any] = {
        "status": "ok",
        "scanned": len(targets),
        "assessed": 0,
        "marked_completed": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }

    from app.domains.alert.service import _target_snapshots
    from app.domains.risk.service import assess_risk
    from app.schemas import RiskAssessRequest

    for target in targets:
        monitor_target_id = _text(target.get("monitor_target_id"))
        company_name = _text(target.get("company_name") or target.get("display_name"))
        target_filter = (
            {"_id": target["_id"]}
            if target.get("_id") is not None
            else {"monitor_target_id": monitor_target_id}
        )
        if not company_name or not monitor_target_id:
            result["skipped"] += 1
            result["errors"].append({
                "monitor_target_id": monitor_target_id or None,
                "error": "监控对象缺少稳定 ID 或企业名称",
            })
            continue

        snapshots = _target_snapshots(db, target, limit=1)
        if snapshots and not force:
            latest_checked_at = snapshots[0].get("checked_at")
            update: dict[str, Any] = {
                "initial_assessment_status": "completed",
            }
            if latest_checked_at:
                update["initial_assessment_at"] = latest_checked_at
            elif not target.get("initial_assessment_at"):
                update["initial_assessment_at"] = datetime.now(timezone.utc)
            collection.update_one(target_filter, {"$set": update, "$unset": {"initial_assessment_error": ""}})
            result["marked_completed"] += 1
            continue

        collection.update_one(target_filter, {"$set": {
            "initial_assessment_status": "running",
            "initial_assessment_started_at": datetime.now(timezone.utc),
        }})
        try:
            assess_risk(RiskAssessRequest(company_name=company_name))
            collection.update_one(target_filter, {"$set": {
                "initial_assessment_status": "completed",
                "initial_assessment_at": datetime.now(timezone.utc),
            }, "$unset": {"initial_assessment_error": "", "initial_assessment_failed_at": ""}})
            result["assessed"] += 1
        except Exception as exc:
            message = str(exc)[:500]
            collection.update_one(target_filter, {"$set": {
                "initial_assessment_status": "failed",
                "initial_assessment_error": message,
                "initial_assessment_failed_at": datetime.now(timezone.utc),
            }})
            result["failed"] += 1
            result["errors"].append({"monitor_target_id": monitor_target_id, "company_name": company_name, "error": message})

    if result["failed"]:
        result["status"] = "partial_failed"
    return result


def sync_supplier_responsibilities(
    client: FeishuBitableClient | None = None,
) -> dict[str, Any]:
    """Sync Feishu ownership rows after formal supplier master data is current."""
    if not settings.FEISHU_BITABLE_ENABLED:
        return {"enabled": False, "status": "disabled", "synced": 0, "invalid": 0}
    if settings.DEMO_DATA_FREEZE:
        return {
            "enabled": True,
            "frozen": True,
            "status": "frozen",
            "synced": 0,
            "invalid": 0,
            "message": "比赛演示数据已冻结，未触发飞书责任分配同步",
        }
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
    linked_users = _link_application_users()
    monitor_sync = _sync_monitor_targets(records)
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
        "linked_users": linked_users,
        "auto_monitored": monitor_sync["monitored"],
        # Keep the existing response shape: initial-assessment failures are
        # surfaced through the established auto-monitor error collection.
        "auto_monitor_errors": monitor_sync["errors"] + monitor_sync["assessment_errors"],
        "errors": [
            {"supplier_code": record["supplier_code"], "reasons": record["validation_errors"]}
            for record in invalid
        ],
    }
