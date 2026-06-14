import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WSManager:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        self._connections[client_id] = websocket
        logger.info("ws_client_connected", client_id=client_id, total=len(self._connections))

    def disconnect(self, client_id: str) -> None:
        self._connections.pop(client_id, None)
        logger.info("ws_client_disconnected", client_id=client_id, total=len(self._connections))

    async def broadcast(self, event: str, payload: dict) -> None:
        """Push an event to all connected clients."""
        message = json.dumps({"event": event, "data": payload})
        dead = []
        for cid, ws in list(self._connections.items()):
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
