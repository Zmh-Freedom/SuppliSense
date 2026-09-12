from types import SimpleNamespace

from app.core.config import settings
from app.domains.alert import notifier
from app.services import feishu
from app.services.ws_manager import WSManager
from app.domains.alert.api_notifications import _notification_scope
from app.schemas.user import UserInDB, UserRole
from datetime import datetime, timezone


class _Response:
    is_success = True

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Collection:
    def __init__(self):
        self.rows = []

    def insert_one(self, document):
        document = dict(document)
        document.setdefault("_id", f"id-{len(self.rows) + 1}")
        self.rows.append(document)
        return SimpleNamespace(inserted_id=document["_id"])

    def update_one(self, query, update):
        for row in self.rows:
            if row.get("_id") == query.get("_id"):
                row.update(update.get("$set", {}))

    def find_one(self, query, **_kwargs):
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items() if not isinstance(value, dict)):
                return row
        return None


class _Db:
    def __init__(self):
        self.collections = {
            "notifications": _Collection(),
            "notification_deliveries": _Collection(),
        }

    def __getitem__(self, name):
        return self.collections[name]


def test_feishu_user_message_uses_app_endpoint_and_open_id(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "FEISHU_APP_ID", "app-id")
    monkeypatch.setattr(settings, "FEISHU_APP_SECRET", "app-secret")
    monkeypatch.setattr(settings, "FEISHU_BITABLE_BASE_URL", "https://feishu.test")
    monkeypatch.setattr(feishu, "_tenant_access_token", None)
    monkeypatch.setattr(feishu, "_tenant_access_token_expires_at", 0.0)

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("tenant_access_token/internal"):
            return _Response({"code": 0, "data": {"tenant_access_token": "tenant-token", "expire": 7200}})
        return _Response({"code": 0})

    monkeypatch.setattr(feishu.httpx, "post", fake_post)
    assert feishu.send_user_message("ou-purchaser", "风险提醒") is True
    assert calls[1][0].endswith("/open-apis/im/v1/messages")
    assert calls[1][1]["params"] == {"receive_id_type": "open_id"}
    assert calls[1][1]["json"]["receive_id"] == "ou-purchaser"


def test_notification_delivery_records_each_retry(monkeypatch):
    db = _Db()
    attempts = {"count": 0}

    def fail_send(_open_id, _text):
        attempts["count"] += 1
        return False

    monkeypatch.setattr("app.services.feishu.send_user_message", fail_send)
    status = notifier._deliver_notification(
        db,
        "notification-1",
        {"recipient_open_id": "ou-purchaser"},
        "风险提醒",
    )
    assert status == "failed"
    assert attempts["count"] == notifier.DELIVERY_MAX_ATTEMPTS
    assert len(db.collections["notification_deliveries"].rows) == notifier.DELIVERY_MAX_ATTEMPTS
    assert all(row["status"] == "failed" for row in db.collections["notification_deliveries"].rows)


def test_notification_scope_uses_feishu_identity_for_non_admin(monkeypatch):
    monkeypatch.setattr("app.domains.supplier.access.feishu_open_id_for_user", lambda _user_id: "ou-purchaser")
    user = UserInDB(
        id="user-1",
        username="buyer",
        email="buyer@example.com",
        role=UserRole.ANALYST,
        password_hash="hash",
        created_at=datetime.now(timezone.utc),
    )
    assert _notification_scope(user) == {"$or": [{"purchaser_open_id": "ou-purchaser"}, {"manager_open_id": "ou-purchaser"}]}


def test_websocket_broadcast_filters_recipient_open_id():
    class _Socket:
        def __init__(self):
            self.messages = []

        async def send_text(self, message):
            self.messages.append(message)

    async def run():
        manager = WSManager()
        buyer_socket = _Socket()
        other_socket = _Socket()
        await manager.connect(buyer_socket, "buyer", open_id="ou-buyer")
        await manager.connect(other_socket, "other", open_id="ou-other")
        await manager.broadcast("risk_alert", {"company": "供应商 A"}, {"ou-buyer"})
        return buyer_socket.messages, other_socket.messages

    import asyncio

    buyer_messages, other_messages = asyncio.run(run())
    assert len(buyer_messages) == 1
    assert other_messages == []


def test_websocket_broadcast_from_thread_runs_without_event_loop(monkeypatch):
    manager = WSManager()
    manager._connections["client"] = object()
    calls = []

    async def fake_broadcast(event, payload, recipient_open_ids):
        calls.append((event, payload, recipient_open_ids))

    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    manager.broadcast_from_thread("sentiment_ready", {"company_name": "供应商 A"})

    assert calls == [("sentiment_ready", {"company_name": "供应商 A"}, None)]
