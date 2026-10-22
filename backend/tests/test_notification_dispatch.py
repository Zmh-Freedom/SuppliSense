from types import SimpleNamespace
import asyncio
from datetime import datetime, timezone
from uuid import uuid4
from bson import ObjectId

import pytest

from app.core.config import settings
from app.domains.alert import notifier
from app.domains.outbox import notification_delivery as delivery
from app.services import feishu
from tests.test_notification_delivery import _Db


def test_independent_notification_only_enqueues_without_sending(monkeypatch):
    db, calls = _Db(), []
    monkeypatch.setattr(settings, "DELIVERY_ROLE_ENABLED", False)
    monkeypatch.setattr(delivery, "queue_notification", lambda *args: calls.append(args) or {"duplicate": False})
    monkeypatch.setattr(feishu, "send_user_message", lambda *a, **kw: pytest.fail("must enqueue only"))
    status = notifier._create_and_deliver(db, {"monitor_target_id": "target"}, {"recipient_open_id": "buyer"}, notification_type="risk_alert", text="private")
    assert status == "queued"
    assert calls[0][0]["monitor_target_id"] == "target"
    assert db["notifications"].rows == []


@pytest.mark.parametrize("active,recipients,target_exists,expected", [
    (True, [{"recipient_open_id": "buyer"}], True, True),
    (False, [{"recipient_open_id": "buyer"}], True, False),
    (True, [{"recipient_open_id": "someone-else"}], True, False),
    (True, [{"recipient_open_id": "buyer"}], False, False),
])
def test_dispatch_rechecks_current_recipient_scope(monkeypatch, active, recipients, target_exists, expected):
    monkeypatch.setattr("app.domains.alert.service.get_watchlist_targets", lambda: [{"monitor_target_id": "target"}] if target_exists else [])
    monkeypatch.setattr(notifier, "get_target_recipients", lambda _: recipients)
    monkeypatch.setattr("app.domains.auth.service.get_user_by_id", lambda _: SimpleNamespace(is_active=active))
    row = {"document": {"monitor_target_id": "target"}, "recipient": {"recipient_open_id": "buyer", "recipient_user_id": "user"}}
    assert delivery._recipient_still_authorized(row) is expected


def test_feishu_unknown_result_is_distinct_from_known_rejection(monkeypatch):
    monkeypatch.setattr(feishu, "_get_tenant_access_token", lambda: "test-token")
    monkeypatch.setattr(feishu.httpx, "post", lambda *a, **kw: SimpleNamespace(is_success=False, status_code=500))
    assert feishu.send_user_message("buyer", "test") is False  # legacy bool contract
    with pytest.raises(RuntimeError, match="未知"):
        feishu.send_user_message("buyer", "test", raise_on_unknown=True)
    monkeypatch.setattr(feishu.httpx, "post", lambda *a, **kw: SimpleNamespace(is_success=True, status_code=200, json=lambda: {"code": 40001}))
    assert feishu.send_user_message("buyer", "test", raise_on_unknown=True) is False


@pytest.mark.parametrize("mongo_id", [ObjectId(), str(uuid4())])
def test_notification_read_supports_both_id_formats_without_losing_scope(monkeypatch, mongo_id):
    from app.domains.alert import api_notifications
    calls = []
    monkeypatch.setattr("app.db.mongo.get_db", lambda: {"notifications": SimpleNamespace(update_one=lambda *a: calls.append(a))})
    monkeypatch.setattr(api_notifications, "_notification_scope", lambda _: {"purchaser_open_id": "buyer"})
    assert asyncio.run(api_notifications.mark_read(str(mongo_id), SimpleNamespace())) == {"status": "ok"}
    assert calls == [({"_id": mongo_id, "purchaser_open_id": "buyer"}, {"$set": {"read": True}})]


def test_bad_notification_id_keeps_existing_noop_response(monkeypatch):
    from app.domains.alert import api_notifications
    monkeypatch.setattr("app.db.mongo.get_db", lambda: {"notifications": SimpleNamespace(update_one=lambda *a: pytest.fail("no broad update"))})
    assert asyncio.run(api_notifications.mark_read("invalid", SimpleNamespace())) == {"status": "ok"}


@pytest.mark.parametrize("formal", [True, False])
def test_revoked_formal_supplier_never_falls_back_to_historical_owner(monkeypatch, formal):
    from app.domains.alert.service import _can_access_target
    target = {"monitor_target_id": "target", "owner_user_id": "old-buyer", "supplier_id": "supplier" if formal else None, "responsibility_status": "unassigned"}
    monkeypatch.setattr("app.domains.supplier.access.list_supplier_assignments", lambda _: [])
    monkeypatch.setattr("app.domains.supplier.access.feishu_open_id_for_user", lambda _: "buyer")
    monkeypatch.setattr("app.domains.supplier.access.can_access_supplier", lambda *a: False)
    monkeypatch.setattr("app.domains.alert.service.get_watchlist_targets", lambda: [target])
    monkeypatch.setattr("app.domains.auth.service.get_user_by_id", lambda _: SimpleNamespace(is_active=True))
    row = {"document": {"monitor_target_id": "target"}, "recipient": {"recipient_open_id": "buyer", "recipient_user_id": "old-buyer"}}
    assert _can_access_target(target, "old-buyer", "analyst") is (not formal)
    assert delivery._recipient_still_authorized(row) is (not formal)


def test_internal_projection_version_is_not_added_to_notification_api(monkeypatch):
    from app.domains.alert import api_notifications
    now = datetime.now(timezone.utc)
    row = {"_id": str(uuid4()), "created_at": now, "_delivery_updated_at": now, "_delivery_version": 3, "read": False}
    cursor = SimpleNamespace(sort=lambda *a: SimpleNamespace(limit=lambda n: [dict(row)]))
    collection = SimpleNamespace(find=lambda q: cursor, count_documents=lambda q: 1)
    monkeypatch.setattr("app.db.mongo.get_db", lambda: {"notifications": collection})
    monkeypatch.setattr(api_notifications, "_notification_scope", lambda _: {"purchaser_open_id": "buyer"})
    result = asyncio.run(api_notifications.list_notifications(None, None, 50, SimpleNamespace()))
    assert "_delivery_updated_at" not in result["notifications"][0]
    assert "_delivery_version" not in result["notifications"][0]
    assert result["unread_count"] == 1
