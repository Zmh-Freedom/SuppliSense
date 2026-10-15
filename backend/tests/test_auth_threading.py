"""Authentication work must not block the async event loop."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, Response

from app.domains.auth import api as auth_api
from app.domains.auth.audit import AuditUnavailable
from app.schemas.user import UserCreate, UserRole


def test_login_json_authentication_runs_off_event_loop(monkeypatch) -> None:
    caller_thread = threading.get_ident()
    observed: list[int] = []
    audit_calls: list[dict] = []

    def fake_authenticate(_username: str, _password: str) -> SimpleNamespace:
        observed.append(threading.get_ident())
        return SimpleNamespace(id="user-1", username="analyst", role="analyst", is_active=True)

    monkeypatch.setattr(auth_api, "authenticate_user", fake_authenticate)
    monkeypatch.setattr(
        auth_api, "log_action", lambda action, **kwargs: audit_calls.append({"action": action, **kwargs})
    )
    monkeypatch.setattr(
        auth_api,
        "_create_tokens",
        lambda _user: SimpleNamespace(access_token="access", refresh_token="refresh"),
    )
    request = Request({"type": "http", "method": "POST", "path": "/auth/login/json", "headers": []})
    response = Response()

    async def run() -> dict:
        return await auth_api.login_json(
            request, auth_api.LoginRequest(username="analyst", password="secret"), response
        )

    result = asyncio.run(run())

    assert result["detail"] == "登录成功"
    assert observed and observed[0] != caller_thread
    assert audit_calls[0]["action"] == "auth.login"
    assert audit_calls[0]["user_id"] == "user-1"


def test_register_stops_before_user_creation_when_required_audit_fails(monkeypatch) -> None:
    created: list[str] = []
    monkeypatch.setattr(
        auth_api, "create_user", lambda user: created.append(user.username)
    )
    monkeypatch.setattr(
        auth_api, "log_action",
        lambda _action, **kwargs: (_ for _ in ()).throw(AuditUnavailable("audit unavailable"))
        if kwargs.get("required") else None,
    )
    request = Request({"type": "http", "method": "POST", "path": "/auth/register", "headers": []})
    admin = SimpleNamespace(id="admin-1", role=UserRole.ADMIN)
    user_data = UserCreate(
        username="newuser", email="new@example.com", password="StrongPass123", role=UserRole.VIEWER
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(auth_api.register(request, user_data, admin))

    assert raised.value.status_code == 503
    assert created == []
