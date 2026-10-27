"""Best effort invalidation must retain scope and trigger reconnect reconciliation."""

import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.services import realtime
from app.services.ws_manager import WSManager, ws_manager


def test_realtime_channel_can_be_overridden_for_isolated_deployments():
    channel = "codex:3d:channel-test"
    source = (
        "from app.core.config import settings; "
        "from app.services import realtime; "
        f"assert settings.REALTIME_CHANNEL == {channel!r}; "
        f"assert realtime.CHANNEL == {channel!r}"
    )
    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        env={**os.environ, "REALTIME_CHANNEL": channel},
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def broadcasts(monkeypatch):
    calls = []
    monkeypatch.setattr(ws_manager, "broadcast_from_thread", lambda *args: calls.append(args))
    return calls


def test_publish_uses_bus_once_without_duplicate_local_broadcast(monkeypatch, broadcasts):
    messages = []
    monkeypatch.setattr(realtime, "cache_client", SimpleNamespace(publish=lambda c, m: messages.append((c, json.loads(m))) or 2))
    assert realtime.publish("risk_alert", {"company": "A"}, {"buyer"})
    assert messages[0][1]["recipients"] == ["buyer"]
    assert broadcasts == []


@pytest.mark.parametrize("failure", [False, True])
def test_publish_local_fallback_keeps_scope(monkeypatch, broadcasts, failure):
    def publish(*args):
        if failure:
            raise ConnectionError("offline")
        return 0
    monkeypatch.setattr(realtime, "cache_client", SimpleNamespace(publish=publish))
    assert realtime.publish("risk_alert", {}, []) is False
    assert broadcasts == [("risk_alert", {}, [])]


@pytest.mark.parametrize("recipients", [None, [], ["buyer"]])
def test_dispatch_preserves_none_and_empty_recipient_semantics(broadcasts, recipients):
    realtime._dispatch(json.dumps({"event": "risk_alert", "data": {}, "recipients": recipients}))
    assert broadcasts == [("risk_alert", {}, recipients)]


@pytest.mark.parametrize("raw", ['no json', '[]', '{"event":"x","data":[]}', '{"event":"x","data":{},"recipients":"buyer"}'])
def test_bad_bus_message_is_ignored(broadcasts, raw):
    realtime._dispatch(raw)
    assert broadcasts == []


def test_subscribe_confirmation_requests_local_reconciliation(broadcasts, monkeypatch):
    subscriber = realtime.RealtimeSubscriber()
    messages = iter([{"type": "subscribe"}, {"type": "message", "data": '{"event":"company_updated","data":{}}'}])

    def read(**kwargs):
        try:
            return next(messages)
        except StopIteration:
            subscriber._stop.set()
            return None

    closed = []
    pubsub = SimpleNamespace(subscribe=lambda c: None, get_message=read, close=lambda: closed.append(True))
    monkeypatch.setattr(realtime, "cache_client", SimpleNamespace(pubsub=lambda: pubsub))
    subscriber._run()
    assert broadcasts == [("realtime_resync", {}, None), ("company_updated", {}, None)]
    assert closed == [True]


def test_explicit_empty_scope_is_never_broadcast_to_any_socket():
    class Socket:
        def __init__(self):
            self.messages = []
        async def send_text(self, value):
            self.messages.append(value)

    async def exercise():
        manager, socket = WSManager(), Socket()
        await manager.connect(socket, "socket", open_id="buyer")
        await manager.broadcast("risk_alert", {"company": "private"}, [])
        assert socket.messages == []
        await manager.broadcast("company_updated", {}, None)
        assert len(socket.messages) == 1

    asyncio.run(exercise())


def test_stop_leaves_pubsub_close_to_its_own_subscriber_thread():
    subscriber = realtime.RealtimeSubscriber()
    subscriber._pubsub = SimpleNamespace(close=lambda: pytest.fail("PubSub.close is not thread-safe"))
    subscriber.stop()
    assert subscriber._stop.is_set()
