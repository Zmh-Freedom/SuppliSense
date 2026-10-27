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


def _mock_verified_company_identity(monkeypatch, company_ids: dict[str, str]) -> None:
    def resolve(name: str) -> dict:
        company_id = company_ids.get(name)
        if company_id is None:
            return {"resolution": "pending_verification", "exact": None}
        return {
            "resolution": "exact",
            "exact": {
                "company_id": company_id,
                "verification_status": "verified",
            },
        }

    monkeypatch.setattr("app.domains.company.service.search_identity", resolve)


def test_paid_refresh_rejects_viewer(monkeypatch) -> None:
    monkeypatch.setattr(
        alert_api,
        "run_refresh_for_target",
        lambda *_args: (_ for _ in ()).throw(AssertionError("paid call")),
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
    refresh_calls: list[tuple[dict, str]] = []
    monkeypatch.setattr(
        alert_api,
        "get_watchlist_targets",
        lambda user_id, role: [{
            "company_name": "甲公司",
            "monitor_target_id": "target-1",
            "company_id": "company-1",
            "identity_status": "verified",
        }],
    )
    monkeypatch.setattr(
        alert_api,
        "run_refresh_for_target",
        lambda target, actor_id: refresh_calls.append((target, actor_id)) or {"status": "ok"},
    )
    _mock_verified_company_identity(monkeypatch, {"甲公司": "company-1"})
    monkeypatch.setattr(alert_api, "reserve_paid_refresh", lambda _user_id, _cost: None)
    monkeypatch.setattr(
        alert_api, "log_action", lambda action, **kwargs: audit_calls.append({"action": action, **kwargs}),
        raising=False,
    )

    response = _client(UserRole.PURCHASER).post(
        "/api/v1/alerts/refresh", json={"company_name": "甲公司"}
    )

    assert response.status_code == 200
    assert [call["action"] for call in audit_calls] == [
        "alert.paid_refresh_requested", "alert.paid_refresh"
    ]
    assert audit_calls[0]["user_id"] == "test-user"
    assert audit_calls[0]["ip_address"]
    assert refresh_calls[0][1] == "test-user"


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


def test_paid_refresh_stops_before_provider_when_quota_is_exhausted(monkeypatch) -> None:
    from app.core.paid_quota import PaidQuotaExceeded

    called: list[str] = []
    monkeypatch.setattr(alert_api, "log_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        alert_api,
        "get_watchlist_targets",
        lambda *_args: [{
            "company_name": "甲公司",
            "monitor_target_id": "target-1",
            "company_id": "company-1",
            "identity_status": "verified",
        }],
    )
    _mock_verified_company_identity(monkeypatch, {"甲公司": "company-1"})
    monkeypatch.setattr(
        alert_api,
        "reserve_paid_refresh",
        lambda _user_id, _cost: (_ for _ in ()).throw(PaidQuotaExceeded()),
        raising=False,
    )
    monkeypatch.setattr(
        alert_api,
        "run_refresh_for_target",
        lambda *_args: called.append("provider") or {"status": "ok"},
    )

    response = _client(UserRole.PURCHASER).post(
        "/api/v1/alerts/refresh", json={"company_name": "甲公司"}
    )

    assert response.status_code == 429
    assert called == []


def test_paid_refresh_all_reserves_for_each_watched_company(monkeypatch) -> None:
    reserved: list[tuple[str, int]] = []
    refreshes: list[tuple[list[dict], str]] = []
    visible_targets = [
        {
            "company_name": name,
            "monitor_target_id": f"target-{index}",
            "company_id": f"company-{index}",
            "identity_status": "verified",
        }
        for index, name in enumerate(("甲公司", "乙公司", "丙公司"), 1)
    ]
    _mock_verified_company_identity(
        monkeypatch,
        {target["company_name"]: target["company_id"] for target in visible_targets},
    )
    monkeypatch.setattr(
        alert_api,
        "get_watchlist_targets",
        lambda user_id, role: visible_targets,
    )
    monkeypatch.setattr(
        alert_api, "reserve_paid_refresh", lambda user_id, cost: reserved.append((user_id, cost))
    )
    monkeypatch.setattr(
        alert_api,
        "run_refresh_all",
        lambda *, targets, actor_id: refreshes.append((targets, actor_id))
        or {"checked": len(targets), "alerts": []},
    )
    monkeypatch.setattr(alert_api, "log_action", lambda *_args, **_kwargs: None)

    response = _client(UserRole.PURCHASER).post("/api/v1/alerts/refresh-all")

    assert response.status_code == 200
    assert reserved == [("test-user", 3)]
    assert refreshes == [(visible_targets, "test-user")]


def test_paid_refresh_rejects_company_outside_visible_verified_targets(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        alert_api,
        "get_watchlist_targets",
        lambda user_id, role: [{
            "company_name": "甲公司",
            "monitor_target_id": "target-1",
            "company_id": "company-1",
            "identity_status": "verified",
        }],
    )
    _mock_verified_company_identity(monkeypatch, {"甲公司": "company-1"})
    monkeypatch.setattr(
        alert_api, "reserve_paid_refresh", lambda *_args: called.append("quota")
    )
    monkeypatch.setattr(
        alert_api, "run_refresh_for_target", lambda *_args: called.append("provider")
    )
    monkeypatch.setattr(alert_api, "log_action", lambda *_args, **_kwargs: None)

    response = _client(UserRole.PURCHASER).post(
        "/api/v1/alerts/refresh", json={"company_name": "乙公司"}
    )

    assert response.status_code == 404
    assert called == []


def test_paid_refresh_rejects_unverified_company_id_before_quota_or_provider(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        alert_api,
        "get_watchlist_targets",
        lambda user_id, role: [{
            "company_name": "甲公司",
            "monitor_target_id": "target-1",
            "company_id": "caller-supplied-unverified-id",
            "identity_status": "verified",
        }],
    )
    _mock_verified_company_identity(monkeypatch, {})
    monkeypatch.setattr(
        alert_api, "reserve_paid_refresh", lambda *_args: called.append("quota")
    )
    monkeypatch.setattr(
        alert_api, "run_refresh_for_target", lambda *_args: called.append("provider")
    )
    monkeypatch.setattr(alert_api, "log_action", lambda *_args, **_kwargs: None)

    response = _client(UserRole.PURCHASER).post(
        "/api/v1/alerts/refresh", json={"company_name": "甲公司"}
    )

    assert response.status_code == 404
    assert called == []


def test_paid_refresh_stops_before_provider_when_required_audit_fails(monkeypatch) -> None:
    from app.domains.auth.audit import AuditUnavailable

    called: list[str] = []
    monkeypatch.setattr(
        alert_api,
        "log_action",
        lambda _action, **kwargs: (_ for _ in ()).throw(AuditUnavailable("audit unavailable"))
        if kwargs.get("required") else None,
    )
    monkeypatch.setattr(alert_api, "reserve_paid_refresh", lambda *_args: called.append("reserved"))
    monkeypatch.setattr(
        alert_api,
        "run_refresh_for_target",
        lambda *_args: called.append("provider"),
    )

    response = _client(UserRole.PURCHASER).post(
        "/api/v1/alerts/refresh", json={"company_name": "甲公司"}
    )

    assert response.status_code == 503
    assert called == []
