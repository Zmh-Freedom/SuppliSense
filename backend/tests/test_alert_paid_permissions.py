"""Role checks for paid alert refresh operations."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from app.core.deps import get_current_user
from app.domains.alert import api as alert_api
from app.schemas.user import UserRole


def _client(role: UserRole) -> TestClient:
    app = FastAPI()
    app.include_router(alert_api.router, prefix="/api/v1/alerts")
    app.dependency_overrides[get_current_user] = lambda: type(
        "User", (), {"id": "test-user", "role": role}
    )()
    return TestClient(app)


def test_paid_refresh_rejects_viewer(monkeypatch) -> None:
    monkeypatch.setattr(
        alert_api, "refresh_company", lambda _name: (_ for _ in ()).throw(AssertionError("paid call"))
    )

    response = _client(UserRole.VIEWER).post(
        "/api/v1/alerts/refresh", json={"company_name": "甲公司"}
    )

    assert response.status_code == 403


def test_paid_refresh_all_rejects_viewer(monkeypatch) -> None:
    monkeypatch.setattr(
        alert_api, "run_refresh_all", lambda: (_ for _ in ()).throw(AssertionError("paid call"))
    )

    response = _client(UserRole.VIEWER).post("/api/v1/alerts/refresh-all")

    assert response.status_code == 403


def test_rule_update_rejects_viewer(monkeypatch) -> None:
    monkeypatch.setattr(
        alert_api, "set_rules", lambda *_args: (_ for _ in ()).throw(AssertionError("write"))
    )

    response = _client(UserRole.VIEWER).put(
        "/api/v1/alerts/rules", json={"rules": []}
    )

    assert response.status_code == 403


def test_paid_refresh_records_actor_and_ip(monkeypatch) -> None:
    audit_calls: list[dict] = []
    monkeypatch.setattr(alert_api, "refresh_company", lambda _name: {"status": "ok"})
    monkeypatch.setattr(
        alert_api, "log_action", lambda action, **kwargs: audit_calls.append({"action": action, **kwargs}),
        raising=False,
    )

    response = _client(UserRole.PURCHASER).post(
        "/api/v1/alerts/refresh", json={"company_name": "甲公司"}
    )

    assert response.status_code == 200
    assert audit_calls[0]["action"] == "alert.paid_refresh"
    assert audit_calls[0]["user_id"] == "test-user"
    assert audit_calls[0]["ip_address"]


def test_alert_status_read_does_not_emit_alert(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        alert_api, "get_latest_snapshot",
        lambda _name: {"checked_at": datetime(2026, 10, 15, tzinfo=timezone.utc)},
    )
    monkeypatch.setattr(
        alert_api,
        "detect_changes",
        lambda _name, **kwargs: calls.append(kwargs) or {"changed": True},
    )

    response = _client(UserRole.VIEWER).get("/api/v1/alerts/status?company_name=甲公司")

    assert response.status_code == 200
    assert calls == [{"emit_alert": False}]
