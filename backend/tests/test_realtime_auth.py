from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.security import create_access_token
from app.main import app


@pytest.mark.parametrize("user", [None, SimpleNamespace(is_active=False)])
def test_websocket_rejects_deleted_or_disabled_user(monkeypatch, user):
    @asynccontextmanager
    async def no_lifespan(_app):
        yield
    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)
    monkeypatch.setattr("app.domains.auth.service.get_user_by_id", lambda _: user)
    monkeypatch.setattr("app.domains.supplier.access.feishu_open_id_for_user", lambda _: None)
    token = create_access_token({"sub": "actor"})
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/ws?token=" + token):
                pass
    assert error.value.code == 4001
