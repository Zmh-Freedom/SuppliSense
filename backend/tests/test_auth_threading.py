"""Authentication work must not block the async event loop."""

import asyncio
import threading
from types import SimpleNamespace

from fastapi import Request, Response

from app.domains.auth import api as auth_api


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
