"""Durable notifications use provider substitutes and isolated server databases."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.core.config import settings
from app.db.init_pg import ensure_pg_schema
from app.db.mongo import get_db
from app.db.postgres import get_cursor

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def schema():
    if settings.PG_DB != "delivery_stage3_test" or settings.MONGO_DB != "delivery_stage3_test":
        pytest.skip("requires isolated delivery_stage3_test")
    ensure_pg_schema()


@pytest.fixture
def notification(monkeypatch):
    from app.domains.outbox import notification_delivery as delivery
    from app.services import realtime
    key = str(uuid4())
    calls = []
    monkeypatch.setattr("app.services.feishu.send_user_message", lambda *a, **kw: calls.append(a) or True)
    monkeypatch.setattr(realtime, "publish", lambda *a: True)
    monkeypatch.setattr(delivery, "_recipient_still_authorized", lambda row: True)
    doc = {"monitor_target_id": key, "recipient_open_id": "test-recipient", "type": "risk_alert", "timestamp": "2026-10-22T00:00:00+00:00"}
    yield delivery, key, doc, calls
    with get_cursor() as (_, cur):
        cur.execute("DELETE FROM outbox_events WHERE event_type='notification.requested' AND aggregate_id IN (SELECT id FROM notification_dispatches WHERE document->>'monitor_target_id'=%s)", (key,))
        cur.execute("DELETE FROM notification_dispatches WHERE document->>'monitor_target_id'=%s RETURNING id", (key,))
        ids = [str(row[0]) for row in cur.fetchall()]
    get_db()["notifications"].delete_many({"_id": {"$in": ids}})
    get_db()["notification_deliveries"].delete_many({"notification_id": {"$in": ids}})


def queued(data):
    delivery, key, doc, calls = data
    return delivery.queue_notification(doc, {"recipient_open_id": "test-recipient"}, "test only", key)


def test_two_notification_requests_share_one_dispatch_and_outbox(notification):
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: queued(notification), range(2)))
    assert rows[0]["id"] == rows[1]["id"]
    with get_cursor() as (_, cur):
        cur.execute("SELECT COUNT(*) FROM outbox_events WHERE aggregate_id=%s AND event_type='notification.requested'", (rows[0]["id"],))
        assert cur.fetchone()[0] == 1


def test_committed_notification_is_not_sent_twice(notification):
    delivery, _, _, calls = notification
    row = queued(notification)
    event = {"payload": {"notification_id": row["id"]}}
    delivery.deliver_notification(event)
    delivery.deliver_notification(event)
    assert len(calls) == 1
    with get_cursor() as (_, cur):
        cur.execute("SELECT status FROM notification_dispatches WHERE id=%s", (row["id"],))
        assert cur.fetchone()[0] == "SENT"
    assert get_db()["notifications"].find_one({"_id": row["id"]})["delivery_status"] == "success"


def test_crashed_call_boundary_becomes_review_without_resending(notification):
    delivery, _, _, calls = notification
    row = queued(notification)
    with get_cursor() as (_, cur):
        cur.execute("UPDATE notification_dispatches SET status='SENDING',attempt_id=%s WHERE id=%s", (str(uuid4()), row["id"]))
    delivery.deliver_notification({"payload": {"notification_id": row["id"]}})
    assert calls == []
    with get_cursor() as (_, cur):
        cur.execute("SELECT status FROM notification_dispatches WHERE id=%s", (row["id"],))
        assert cur.fetchone()[0] == "NEEDS_REVIEW"
    audit = get_db()["notification_deliveries"].find_one({"notification_id": row["id"]})
    assert audit["status"] == "needs_review"


def test_unknown_provider_result_is_retained_for_review(notification, monkeypatch):
    delivery, _, _, calls = notification
    row = queued(notification)
    def uncertain(*a, **kw):
        calls.append(a)
        raise TimeoutError("provider result unknown")
    monkeypatch.setattr("app.services.feishu.send_user_message", uncertain)
    event = {"payload": {"notification_id": row["id"]}}
    delivery.deliver_notification(event)
    delivery.deliver_notification(event)
    assert len(calls) == 1
    assert get_db()["notifications"].find_one({"_id": row["id"]})["delivery_status"] == "needs_review"


def test_known_rejection_is_failed_without_automatic_resend(notification, monkeypatch):
    delivery, _, _, calls = notification
    row = queued(notification)
    monkeypatch.setattr("app.services.feishu.send_user_message", lambda *a, **kw: calls.append(a) or False)
    event = {"payload": {"notification_id": row["id"]}}
    delivery.deliver_notification(event)
    delivery.deliver_notification(event)
    assert len(calls) == 1
    assert get_db()["notifications"].find_one({"_id": row["id"]})["delivery_status"] == "failed"


def test_revoked_recipient_cancels_without_provider_call(notification, monkeypatch):
    delivery, _, _, calls = notification
    row = queued(notification)
    monkeypatch.setattr(delivery, "_recipient_still_authorized", lambda _: False)
    delivery.deliver_notification({"payload": {"notification_id": row["id"]}})
    assert calls == []
    assert get_db()["notifications"].find_one({"_id": row["id"]})["delivery_status"] == "cancelled"


def test_notification_and_outbox_roll_back_together(notification, monkeypatch):
    delivery, key, _, _ = notification
    def fail(*args):
        raise RuntimeError("outbox unavailable")
    monkeypatch.setattr(delivery, "enqueue_event", fail)
    with pytest.raises(RuntimeError):
        queued(notification)
    with get_cursor() as (_, cur):
        cur.execute("SELECT COUNT(*) FROM notification_dispatches WHERE document->>'monitor_target_id'=%s", (key,))
        assert cur.fetchone()[0] == 0


@pytest.mark.parametrize("fail_at", ["QUEUED", "SENT"])
def test_projection_failure_does_not_replay_a_committed_external_effect(notification, monkeypatch, fail_at):
    delivery, _, _, calls = notification
    row = queued(notification)
    original = delivery._project
    def fail(record):
        if record["status"] == fail_at:
            raise RuntimeError("projection unavailable")
        original(record)
    monkeypatch.setattr(delivery, "_project", fail)
    event = {"payload": {"notification_id": row["id"]}}
    with pytest.raises(RuntimeError):
        delivery.deliver_notification(event)
    monkeypatch.setattr(delivery, "_project", original)
    delivery.deliver_notification(event)
    assert len(calls) == 1
    assert get_db()["notifications"].find_one({"_id": row["id"]})["delivery_status"] == "success"


def test_late_projection_never_regresses_notification_or_delivery_audit(notification):
    delivery, _, _, _ = notification
    queued_row = queued(notification)
    with get_cursor() as (_, cur):
        older = delivery._read(cur, queued_row["id"])
    delivery.deliver_notification({"payload": {"notification_id": queued_row["id"]}})
    with get_cursor() as (_, cur):
        newer = delivery._read(cur, queued_row["id"])
    older.update(status="SENDING", attempt_id=newer["attempt_id"])
    # BSON Date truncates microseconds: equal millisecond timestamps cannot
    # authorize an old state to overwrite a newer committed receipt.
    older["updated_at"] = newer["updated_at"].replace(microsecond=(newer["updated_at"].microsecond // 1000) * 1000)
    delivery._project(older)
    assert get_db()["notifications"].find_one({"_id": older["id"]})["delivery_status"] == "success"
    assert get_db()["notification_deliveries"].find_one({"notification_id": older["id"]})["status"] == "success"
