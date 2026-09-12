import json
import logging
import asyncio

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WSManager:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}
        self._identities: dict[str, dict[str, str | None]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def connect(
        self,
        websocket: WebSocket,
        client_id: str,
        *,
        user_id: str | None = None,
        open_id: str | None = None,
        role: str | None = None,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self._connections[client_id] = websocket
        self._identities[client_id] = {"user_id": user_id, "open_id": open_id, "role": role}
        logger.info("ws_client_connected client_id=%s total=%d", client_id, len(self._connections))

    def disconnect(self, client_id: str) -> None:
        self._connections.pop(client_id, None)
        self._identities.pop(client_id, None)
        if not self._connections:
            self._loop = None
        logger.info("ws_client_disconnected client_id=%s total=%d", client_id, len(self._connections))

    def broadcast_from_thread(
        self,
        event: str,
        payload: dict,
        recipient_open_ids: set[str] | list[str] | None = None,
    ) -> None:
        """从同步或工作线程安全地投递广播，不遗留未等待的协程。"""
        if not self._connections:
            return

        coroutine = self.broadcast(event, payload, recipient_open_ids)
        loop = self._loop
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)

            def _log_failure(done: asyncio.Future) -> None:
                try:
                    done.result()
                except Exception as exc:  # pragma: no cover - network best effort
                    logger.warning("ws_broadcast_failed: %s", exc)

            future.add_done_callback(_log_failure)
            return

        try:
            asyncio.run(coroutine)
        except Exception as exc:  # pragma: no cover - network best effort
            logger.warning("ws_broadcast_failed: %s", exc)

    async def broadcast(
        self,
        event: str,
        payload: dict,
        recipient_open_ids: set[str] | list[str] | None = None,
    ) -> None:
        """Push an event to matching Feishu identities, or all clients if unset."""
        message = json.dumps({"event": event, "data": payload})
        recipients = {value for value in (recipient_open_ids or []) if value}
        dead = []
        for cid, ws in list(self._connections.items()):
            identity = self._identities.get(cid, {})
            if recipients and identity.get("open_id") not in recipients:
                continue
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(cid)
        for cid in dead:
            self.disconnect(cid)

    async def send(self, client_id: str, event: str, payload: dict) -> None:
        """Push an event to a specific client."""
        ws = self._connections.get(client_id)
        if ws is None:
            return
        message = json.dumps({"event": event, "data": payload})
        try:
            await ws.send_text(message)
        except Exception:
            self.disconnect(client_id)


ws_manager = WSManager()
