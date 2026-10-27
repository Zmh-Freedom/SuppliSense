"""Cross-process real-time invalidation over Redis pub/sub.

The bus is deliberately best-effort: business state is durable in PostgreSQL
and MongoDB, while a missed invalidation only delays a frontend refetch until
the next event or reconnect.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from app.core.cache import cache_client
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

CHANNEL = settings.REALTIME_CHANNEL


def _encode(event: str, payload: dict[str, Any], recipients: Any) -> str:
    return json.dumps(
        {
            "event": event,
            "data": payload,
            "recipients": None if recipients is None else sorted({str(item) for item in recipients if item}),
        },
        ensure_ascii=False,
        default=str,
    )


def _local_fallback(event: str, payload: dict[str, Any], recipients: Any) -> None:
    try:
        from app.services.ws_manager import ws_manager

        ws_manager.broadcast_from_thread(event, payload, recipients)
    except Exception:
        logger.exception("realtime_local_fallback_failed", event_type=event)


def publish(event: str, payload: dict[str, Any], recipients: Any = None) -> bool:
    """Publish one invalidation; return True when at least one API subscriber got it."""
    try:
        receivers = int(cache_client.publish(CHANNEL, _encode(event, payload, recipients)))
    except Exception:
        logger.exception("realtime_publish_failed", event_type=event)
        _local_fallback(event, payload, recipients)
        return False
    if receivers <= 0:
        # No subscriber (single-process tests or a transient API restart):
        # deliver locally when possible instead of silently dropping the hint.
        _local_fallback(event, payload, recipients)
        return False
    return True


def _dispatch(raw: Any) -> None:
    try:
        message = json.loads(raw)
        event, payload = message.get("event"), message.get("data")
        recipients = message.get("recipients")
        if not isinstance(event, str) or not event or not isinstance(payload, dict):
            raise ValueError("invalid event or data")
        if recipients is not None and (not isinstance(recipients, list) or not all(isinstance(item, str) for item in recipients)):
            raise ValueError("invalid recipient scope")
    except Exception:
        logger.warning("realtime_message_invalid")
        return
    from app.services.ws_manager import ws_manager

    ws_manager.broadcast_from_thread(event, payload, recipients)


class RealtimeSubscriber:
    """One Redis subscriber thread per API process."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pubsub: Any = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="realtime-subscriber", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # PubSub.reset/close is not thread-safe. The subscriber alone closes
        # it in finally; bounded socket/read timeouts let this join finish.
        thread = self._thread
        if thread is not None:
            thread.join(timeout=4)
            if not thread.is_alive():
                self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            pubsub = None
            try:
                pubsub = cache_client.pubsub()
                self._pubsub = pubsub
                pubsub.subscribe(CHANNEL)
                logger.info("realtime_subscriber_started", channel=CHANNEL)
                while not self._stop.is_set():
                    message = pubsub.get_message(timeout=1.0)
                    if message is None:
                        continue
                    if message.get("type") == "subscribe":
                        # Redis can reconnect while browser WS stays open.
                        # Reconcile everything missed during that outage.
                        _local_fallback("realtime_resync", {}, None)
                    elif message.get("type") == "message":
                        _dispatch(message.get("data"))
            except Exception:
                if not self._stop.is_set():
                    logger.exception("realtime_subscriber_failed")
                    self._stop.wait(1.0)
            finally:
                if pubsub is not None:
                    try:
                        pubsub.close()
                    except Exception:
                        pass
                if self._pubsub is pubsub:
                    self._pubsub = None
        logger.info("realtime_subscriber_stopped")


_subscriber = RealtimeSubscriber()


def start_realtime_subscriber() -> None:
    _subscriber.start()


def stop_realtime_subscriber() -> None:
    _subscriber.stop()


def realtime_subscriber_running() -> bool:
    return _subscriber.running


__all__ = [
    "CHANNEL",
    "RealtimeSubscriber",
    "publish",
    "realtime_subscriber_running",
    "start_realtime_subscriber",
    "stop_realtime_subscriber",
]
