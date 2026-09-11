"""按责任人隔离的风险通知生成、投递与审计。"""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

from app.core.logging import get_logger
from app.db.mongo import get_db

logger = get_logger()
SCORE_THRESHOLD = 10
DELIVERY_MAX_ATTEMPTS = 3


def get_target_recipients(target: dict) -> list[dict]:
    """从责任快照解析采购员和科室经理收件人。"""
    from app.domains.supplier.access import (
        feishu_open_id_for_user,
        list_supplier_assignments,
        user_id_for_feishu_open_id,
    )

    records = list_supplier_assignments(str(target["supplier_id"])) if target.get("supplier_id") else []
    recipients: list[dict] = []
    for record in records:
        purchaser_open_id = str(record.get("purchaser_open_id") or "").strip()
        manager_open_id = str(record.get("manager_open_id") or "").strip()
        common = {
            "purchaser_open_id": purchaser_open_id,
            "manager_open_id": manager_open_id,
            "department_code": record.get("department_code"),
            "department_name": record.get("department_name"),
            "purchaser_name": record.get("purchaser_name"),
        }
        if purchaser_open_id:
            recipients.append({
                **common,
                "recipient_type": "purchaser",
                "recipient_open_id": purchaser_open_id,
                "recipient_user_id": user_id_for_feishu_open_id(purchaser_open_id),
            })
        if manager_open_id:
            recipients.append({
                **common,
                "recipient_type": "manager",
                "recipient_open_id": manager_open_id,
                "recipient_user_id": user_id_for_feishu_open_id(manager_open_id),
            })
    if not recipients and target.get("owner_user_id"):
        purchaser_open_id = feishu_open_id_for_user(str(target["owner_user_id"])) or ""
        if purchaser_open_id:
            recipients.append({
                "purchaser_open_id": purchaser_open_id,
                "manager_open_id": None,
                "department_code": None,
                "department_name": None,
                "purchaser_name": None,
                "recipient_type": "purchaser",
                "recipient_open_id": purchaser_open_id,
                "recipient_user_id": str(target["owner_user_id"]),
            })
    unique: dict[tuple[str, str, str], dict] = {}
    for recipient in recipients:
        key = (
            str(recipient.get("recipient_type") or ""),
            str(recipient.get("recipient_open_id") or ""),
            str(recipient.get("purchaser_open_id") or ""),
        )
        if key[1]:
            unique[key] = recipient
    return list(unique.values())


def _record_delivery(db: object, *, notification_id: str, recipient_open_id: str | None, status: str, attempt: int, error: str | None = None) -> None:
    db["notification_deliveries"].insert_one({
        "notification_id": notification_id,
        "recipient_open_id": recipient_open_id,
        "channel": "feishu_app",
        "status": status,
        "attempt": attempt,
        "error": error,
        "created_at": datetime.now(timezone.utc),
    })


def _deliver_notification(db: object, notification_id: str, recipient: dict, text: str) -> str:
    """向单个飞书用户投递，三次尝试均留痕。"""
    from app.services.feishu import send_user_message

    open_id = str(recipient.get("recipient_open_id") or "")
    if not open_id:
        _record_delivery(db, notification_id=notification_id, recipient_open_id=None, status="failed", attempt=1, error="missing_recipient_open_id")
        return "failed"
    for attempt in range(1, DELIVERY_MAX_ATTEMPTS + 1):
        try:
            if send_user_message(open_id, text):
                _record_delivery(db, notification_id=notification_id, recipient_open_id=open_id, status="success", attempt=attempt)
                return "success"
            error = "feishu_api_returned_failure"
        except Exception as exc:  # pragma: no cover - network client guard
            error = str(exc)[:500]
        _record_delivery(db, notification_id=notification_id, recipient_open_id=open_id, status="failed", attempt=attempt, error=error)
    return "failed"


def _create_and_deliver(db: object, target: dict, recipient: dict, *, notification_type: str, text: str, **extra: object) -> str:
    now = datetime.now(timezone.utc)
    fingerprint = hashlib.sha256(
        json.dumps(
            {"target": target.get("monitor_target_id"), "type": notification_type, "extra": extra},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    duplicate = db["notifications"].find_one({
        "monitor_target_id": target.get("monitor_target_id"),
        "recipient_open_id": recipient.get("recipient_open_id"),
        "type": notification_type,
        "event_fingerprint": fingerprint,
        "timestamp": {"$gte": now - timedelta(hours=24)},
    })
    if duplicate:
        return "duplicate"
    document = {
        "type": notification_type,
        "company": target.get("company_name", ""),
        "company_name": target.get("company_name", ""),
        "monitor_target_id": target.get("monitor_target_id"),
        "target_type": target.get("target_type"),
        "supplier_id": target.get("supplier_id"),
        "candidate_id": target.get("candidate_id"),
        "company_id": target.get("company_id"),
        "recipient_user_id": recipient.get("recipient_user_id"),
        "recipient_open_id": recipient.get("recipient_open_id"),
        "recipient_type": recipient.get("recipient_type"),
        "purchaser_open_id": recipient.get("purchaser_open_id"),
        "manager_open_id": recipient.get("manager_open_id"),
        "department_code": recipient.get("department_code"),
        "department_name": recipient.get("department_name"),
        "read": False,
        "delivery_status": "pending",
        "timestamp": now,
        "created_at": now,
        "event_fingerprint": fingerprint,
        **extra,
    }
    result = db["notifications"].insert_one(document)
    notification_id = str(result.inserted_id)
    status = _deliver_notification(db, notification_id, recipient, text)
    db["notifications"].update_one(
        {"_id": result.inserted_id},
        {"$set": {"delivery_status": status, "delivered_at": datetime.now(timezone.utc) if status == "success" else None}},
    )
    return status


def notify_target_change(target: dict, changes: list[dict], severity: str = "warning") -> dict:
    """Immediately deliver a rule-triggered change through the same scoped path."""
    db = get_db()
    recipients = get_target_recipients(target)
    text = "\n".join([
        f"供应商风险变化：{target.get('company_name', '')}",
        "；".join(f"{c.get('field', '')}：{c.get('old', '-')} → {c.get('new', '-')}" for c in changes[:10]),
        "请在系统中查看证据并完成采购复核。",
    ])
    statuses = [
        _create_and_deliver(db, target, recipient, notification_type="risk_alert", text=text, changes=changes, severity=severity)
        for recipient in recipients
    ]
    recipient_ids = {str(item["recipient_open_id"]) for item in recipients if item.get("recipient_open_id")}
    payload = {"company": target.get("company_name", ""), "monitor_target_id": target.get("monitor_target_id"), "changes": changes, "severity": severity}
    _broadcast("risk_alert", payload, recipient_ids)
    _broadcast("alert_update", {**payload, "company_name": target.get("company_name", "")}, recipient_ids)
    return {"recipients": len(recipients), "delivered": statuses.count("success")}


def _broadcast(event: str, payload: dict, recipient_open_ids: set[str]) -> None:
    if not recipient_open_ids:
        return
    from app.services.ws_manager import ws_manager

    async def publish() -> None:
        await ws_manager.broadcast(event, payload, recipient_open_ids)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(publish())
        else:
            asyncio.run(publish())
    except RuntimeError:
        asyncio.run(publish())


def check_and_notify() -> dict:
    """检查监控对象变化，只通知其采购员和所属科室经理。"""
    from app.domains.alert.service import detect_changes, get_watchlist_targets

    targets = get_watchlist_targets()
    if not targets:
        return {"checked": 0, "alerts": []}
    db = get_db()
    alerts: list[dict] = []
    suggestions_sent = 0
    for target in targets:
        company_name = target.get("company_name", "")
        target_id = target.get("monitor_target_id")
        try:
            changes = detect_changes(company_name, monitor_target_id=target_id)
            if not changes.get("changed"):
                continue
            score_delta = 0.0
            level_escalated = False
            for change in changes.get("changes", []):
                if change.get("field") == "风险评分":
                    try:
                        score_delta = max(score_delta, float(change.get("new", 0)) - float(change.get("old", 0)))
                    except (TypeError, ValueError):
                        pass
                elif change.get("field") == "风险等级" and change.get("new") in ("high", "critical"):
                    level_escalated = True
            if score_delta < SCORE_THRESHOLD and not level_escalated:
                continue
            recipients = get_target_recipients(target)
            recipient_ids = {str(item["recipient_open_id"]) for item in recipients if item.get("recipient_open_id")}
            text = "\n".join([
                f"供应商风险变化：{company_name}",
                f"风险变化幅度：{score_delta:g}",
                "；".join(f"{c.get('field', '')}：{c.get('old', '-')} → {c.get('new', '-')}" for c in changes.get("changes", [])[:10]),
                "请在系统中查看证据并完成采购复核。",
            ])
            for recipient in recipients:
                _create_and_deliver(db, target, recipient, notification_type="risk_alert", text=text, score_delta=score_delta, severity=changes.get("severity", "warning"), changes=changes.get("changes", []))
            _broadcast("risk_alert", {"company": company_name, "monitor_target_id": target_id, "score_delta": score_delta, "changes": changes.get("changes", [])}, recipient_ids)
            alerts.append({"company": company_name, "monitor_target_id": target_id, "score_delta": score_delta})

            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            for recipient in recipients:
                if db["notifications"].find_one({"monitor_target_id": target_id, "type": "sourcing_suggestion", "recipient_open_id": recipient.get("recipient_open_id"), "timestamp": {"$gte": cutoff}}):
                    continue
                try:
                    from app.domains.sourcing.service import get_top_alternatives
                    alternatives = get_top_alternatives(company_name, top_k=3)
                    if alternatives:
                        names = "、".join(str(a.get("name") or a.get("company_name") or a) for a in alternatives)
                        _create_and_deliver(db, target, recipient, notification_type="sourcing_suggestion", text=f"{company_name} 风险变化后可查看替代供应商建议：\n{names}", alternatives=alternatives)
                        suggestions_sent += 1
                except Exception as exc:
                    logger.error("sourcing_suggestion_failed", company=company_name, error=str(exc))
        except Exception as exc:
            logger.error("alert_check_failed", company=company_name, error=str(exc))
    return {"checked": len(targets), "alerts": alerts, "suggestions_sent": suggestions_sent}


def get_notifications(limit: int = 20, purchaser_open_id: str | None = None) -> list[dict]:
    """按采购员身份读取通知；没有身份时拒绝无范围查询。"""
    if not purchaser_open_id:
        return []
    db = get_db()
    return list(db["notifications"].find({"purchaser_open_id": purchaser_open_id}, {"_id": 0}).sort("created_at", -1).limit(limit))
