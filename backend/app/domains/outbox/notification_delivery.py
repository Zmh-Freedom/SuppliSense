"""Durable notification requests and conservative external delivery receipts."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from psycopg2.extras import Json

from app.core.config import settings
from app.db.mongo import get_db
from app.db.postgres import get_cursor
from app.domains.outbox.repo import _row_to_dict, enqueue_event

MAX_PROVIDER_ATTEMPTS = 3


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def _send_with_explicit_retry(open_id: str, text: str) -> bool:
    """Retry only explicit non-delivery; an unknown result must not be resent."""
    from app.services.feishu import send_user_message

    for _ in range(MAX_PROVIDER_ATTEMPTS):
        if send_user_message(open_id, text, raise_on_unknown=True):
            return True
    return False


def _dispatch_notification(notification_id: str, open_id: str, text: str) -> bool:
    if settings.OUTBOX_DELIVERY_SINK == "test":
        get_db()["delivery_test_sink_receipts"].update_one(
            {"_id": notification_id},
            {
                "$setOnInsert": {
                    "notification_id": notification_id,
                    "sink": "test",
                    "message_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "created_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        return True
    if settings.OUTBOX_DELIVERY_SINK != "provider":
        raise RuntimeError("unknown notification delivery sink")
    return _send_with_explicit_retry(open_id, text)


def _read(cur, notification_id: str) -> dict[str, Any]:
    cur.execute("SELECT * FROM notification_dispatches WHERE id=%s FOR UPDATE", (notification_id,))
    row = _row_to_dict(cur, cur.fetchone())
    if row is None:
        raise ValueError("通知投递记录不存在")
    row["id"] = str(row["id"])
    return row


def queue_notification(document: dict, recipient: dict, text: str, fingerprint: str) -> dict[str, Any]:
    key = hashlib.sha256(json.dumps({
        "fingerprint": fingerprint, "target": document.get("monitor_target_id"),
        "recipient": recipient.get("recipient_open_id"), "type": document.get("type"),
    }, sort_keys=True).encode()).hexdigest()
    with get_cursor() as (_, cur):
        # Serialize this specific target/recipient/fingerprint, not the queue.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (key,))
        cur.execute("SELECT * FROM notification_dispatches WHERE dedupe_key=%s AND created_at>=clock_timestamp()-INTERVAL '24 hours' ORDER BY created_at DESC LIMIT 1", (key,))
        row = _row_to_dict(cur, cur.fetchone())
        if row is not None:
            return {**row, "id": str(row["id"]), "duplicate": True}
        notification_id = str(uuid4())
        cur.execute("INSERT INTO notification_dispatches(id,dedupe_key,document,recipient,message_text) VALUES(%s,%s,%s,%s,%s) RETURNING *", (notification_id, key, Json(_json(document)), Json(_json(recipient)), text))
        row = _row_to_dict(cur, cur.fetchone())
        enqueue_event(cur, "notification.requested", "notification", notification_id, {"notification_id": notification_id})
        return {**row, "id": notification_id, "duplicate": False}


def _recipient_still_authorized(row: dict[str, Any]) -> bool:
    from app.domains.alert.notifier import get_target_recipients
    from app.domains.alert.service import get_watchlist_targets
    from app.domains.auth.service import get_user_by_id

    target = next((item for item in get_watchlist_targets() if str(item.get("monitor_target_id")) == str(row["document"].get("monitor_target_id"))), None)
    if target is None:
        return False
    recipient = row["recipient"]
    if recipient.get("recipient_user_id"):
        user = get_user_by_id(recipient["recipient_user_id"])
        if user is None or not user.is_active:
            return False
    open_id = recipient.get("recipient_open_id")
    return bool(open_id and any(item.get("recipient_open_id") == open_id for item in get_target_recipients(target)))


def _project_values(collection: Any, key: str, values: dict[str, Any], version: int) -> None:
    newer = {"$lte": [{"$ifNull": ["$_delivery_version", 0]}, version]}
    values = {**values, "_delivery_version": version}
    collection.update_one({"_id": key}, [{"$set": {
        name: {"$cond": [newer, {"$literal": value}, f"${name}"]}
        for name, value in values.items()
    }}], upsert=True)


def _project(row: dict[str, Any]) -> None:
    document = dict(row["document"])
    for key in ("timestamp", "created_at"):
        if isinstance(document.get(key), str):
            document[key] = datetime.fromisoformat(document[key])
    document.pop("_id", None)
    status = {"SENT": "success", "FAILED": "failed", "CANCELLED": "cancelled", "NEEDS_REVIEW": "needs_review"}.get(row["status"], "pending")
    db = get_db()
    db["notifications"].update_one({"_id": row["id"]}, {"$setOnInsert": document}, upsert=True)
    _project_values(db["notifications"], row["id"], {"delivery_status": status, "delivered_at": row["updated_at"] if row["status"] == "SENT" else None}, row["version"])
    if row.get("attempt_id"):
        _project_values(db["notification_deliveries"], f"{row['id']}:{row['attempt_id']}", {"notification_id": row["id"], "recipient_open_id": row["recipient"].get("recipient_open_id"), "status": status, "attempt": 1, "error": row.get("diagnostic"), "created_at": row["updated_at"]}, row["version"])


def deliver_notification(event: dict) -> None:
    notification_id = str(event["payload"]["notification_id"])
    with get_cursor() as (_, cur):
        row = _read(cur, notification_id)
    _project(row)  # A projection failure before the call is safe to retry.
    if row["status"] not in {"QUEUED", "SENDING"}:
        return
    authorized = _recipient_still_authorized(row) if row["status"] == "QUEUED" else False
    attempt = str(uuid4())
    with get_cursor() as (_, cur):
        row = _read(cur, notification_id)
        if row["status"] == "SENDING":
            # A predecessor crossed the external-call boundary. Its result
            # is unknowable; a new consumer must never blindly resend.
            cur.execute("UPDATE notification_dispatches SET status='NEEDS_REVIEW',diagnostic='external_result_unknown',version=version+1,updated_at=clock_timestamp() WHERE id=%s", (notification_id,))
        elif row["status"] == "QUEUED":
            cur.execute("UPDATE notification_dispatches SET status=%s,attempt_id=%s,diagnostic=%s,version=version+1,updated_at=clock_timestamp() WHERE id=%s", ("SENDING" if authorized else "CANCELLED", attempt, None if authorized else "recipient_scope_revoked", notification_id))
        row = _read(cur, notification_id)
    if row["status"] != "SENDING" or str(row.get("attempt_id")) != attempt:
        _project(row)
        return
    try:
        sent = _dispatch_notification(
            notification_id,
            row["recipient"].get("recipient_open_id", ""),
            row["message_text"],
        )
        status, diagnostic = ("SENT", None) if sent else ("FAILED", "provider_rejected")
    except Exception:
        status, diagnostic = "NEEDS_REVIEW", "external_result_unknown"
    with get_cursor() as (_, cur):
        cur.execute("UPDATE notification_dispatches SET status=%s,diagnostic=%s,version=version+1,updated_at=clock_timestamp() WHERE id=%s AND status='SENDING' AND attempt_id=%s", (status, diagnostic, notification_id, attempt))
        row = _read(cur, notification_id)
    _project(row)
