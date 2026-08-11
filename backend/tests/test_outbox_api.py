"""Contract tests for the administrator transactional-Outbox API."""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.core.security import create_access_token
from app.schemas.user import UserInDB


EVENT_ID = "00000000-0000-4000-8000-000000000901"


def _request(client, method: str, path: str, body: dict | None, **kwargs):
    if body is None:
        return getattr(client, method)(path, **kwargs)
    return getattr(client, method)(path, json=body, **kwargs)


def _analyst_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Return a valid analyst token without persisting a user."""
    analyst = UserInDB(
        id="00000000-0000-0000-0000-000000000010",
        username="p1-outbox-analyst",
        email="p1-outbox-analyst@example.com",
        role="analyst",
        password_hash="unused",
        created_at=datetime.now(timezone.utc),
        is_active=True,
    )
    monkeypatch.setattr("app.core.deps.get_user_by_id", lambda user_id: analyst)
    return create_access_token({"sub": analyst.id})


@pytest.fixture
def admin_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Authenticate an in-memory admin without coupling this API contract to registration."""
    admin = UserInDB(
        id="00000000-0000-0000-0000-000000000001",
        username="p1-outbox-admin",
        email="p1-outbox-admin@example.com",
        role="admin",
        password_hash="unused",
        created_at=datetime.now(timezone.utc),
        is_active=True,
    )
    monkeypatch.setattr("app.core.deps.get_user_by_id", lambda user_id: admin)
    return {"Authorization": f"Bearer {create_access_token({'sub': admin.id})}"}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    (
        ("get", "/api/v1/admin/outbox/events", None),
        ("post", f"/api/v1/admin/outbox/events/{EVENT_ID}/replay", {"reason": "重放事件"}),
    ),
)
def test_outbox_admin_endpoints_require_authentication(
    client,
    method: str,
    path: str,
    body: dict | None,
) -> None:
    """Dropping authentication would expose event operations to anonymous callers."""
    response = _request(client, method, path, body)

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "HTTP_401", "message": "未提供认证凭据", "detail": None}
    }


@pytest.mark.parametrize(
    ("method", "path", "body"),
    (
        ("get", "/api/v1/admin/outbox/events", None),
        ("post", f"/api/v1/admin/outbox/events/{EVENT_ID}/replay", {"reason": "重放事件"}),
    ),
)
def test_outbox_admin_endpoints_require_admin(
    client,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict | None,
) -> None:
    """Removing the role guard would expose event operations to analysts."""
    token = _analyst_token(monkeypatch)

    response = _request(
        client,
        method,
        path,
        body,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": {"code": "HTTP_403", "message": "需要权限: admin", "detail": None}
    }


def test_outbox_events_rejects_unknown_status(client, admin_headers: dict[str, str]) -> None:
    """Accepting arbitrary status values could produce an accidental unbounded query."""
    response = client.get(
        "/api/v1/admin/outbox/events?status=published",
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "请求参数校验失败",
            "detail": [
                {
                    "type": "literal_error",
                    "loc": ["query", "status"],
                    "msg": "Input should be 'pending', 'failed' or 'dead_letter'",
                    "input": "published",
                    "ctx": {"expected": "'pending', 'failed' or 'dead_letter'"},
                }
            ],
        }
    }


@pytest.mark.parametrize("limit", ("0", "101", "not-a-number"))
def test_outbox_events_rejects_limit_outside_strict_bounds(
    client,
    admin_headers: dict[str, str],
    limit: str,
) -> None:
    """Changing the bounds could turn an operations endpoint into a large table scan."""
    response = client.get(
        f"/api/v1/admin/outbox/events?limit={limit}",
        headers=admin_headers,
    )

    assert response.status_code == 422


def test_outbox_events_returns_deterministically_ordered_schema(
    client,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning repository order directly would make the operator view nondeterministic."""
    from app.domains.outbox import api as outbox_api

    monkeypatch.setattr(
        outbox_api,
        "list_events",
        lambda status, limit: [
            {
                "event_id": "00000000-0000-4000-8000-000000000903",
                "event_type": "company.updated",
                "aggregate_type": "company",
                "aggregate_id": "00000000-0000-4000-8000-000000000904",
                "schema_version": 1,
                "payload": {
                    "company_id": "00000000-0000-4000-8000-000000000904",
                    "operator_id": "admin-internal-id",
                    "reason": "内部回放原因",
                },
                "occurred_at": "2026-08-08T00:00:02+00:00",
                "published_at": None,
                "attempt_count": 1,
                "last_error": "token=visible-secret\n" + ("x" * 400),
                "next_attempt_at": "2026-08-08T00:00:04+00:00",
                "locked_by": None,
                "locked_until": None,
                "dead_lettered_at": None,
            },
            {
                "event_id": "00000000-0000-4000-8000-000000000901",
                "event_type": "company.created",
                "aggregate_type": "company",
                "aggregate_id": "00000000-0000-4000-8000-000000000902",
                "schema_version": 1,
                "payload": {"company_id": "00000000-0000-4000-8000-000000000902"},
                "occurred_at": "2026-08-08T00:00:01+00:00",
                "published_at": None,
                "attempt_count": 0,
                "last_error": None,
                "next_attempt_at": "2026-08-08T00:00:01+00:00",
                "locked_by": None,
                "locked_until": None,
                "dead_lettered_at": None,
            },
        ],
    )

    response = client.get(
        "/api/v1/admin/outbox/events?status=failed&limit=2",
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "events": [
            {
                "event_id": "00000000-0000-4000-8000-000000000901",
                "event_type": "company.created",
                "aggregate_type": "company",
                "aggregate_id": "00000000-0000-4000-8000-000000000902",
                "schema_version": 1,
                "status": "pending",
                "occurred_at": "2026-08-08T00:00:01Z",
                "published_at": None,
                "attempt_count": 0,
                "last_error": None,
            },
            {
                "event_id": "00000000-0000-4000-8000-000000000903",
                "event_type": "company.updated",
                "aggregate_type": "company",
                "aggregate_id": "00000000-0000-4000-8000-000000000904",
                "schema_version": 1,
                "status": "failed",
                "occurred_at": "2026-08-08T00:00:02Z",
                "published_at": None,
                "attempt_count": 1,
                "last_error": "delivery_failed",
            },
        ]
    }
    assert "visible-secret" not in str(body)
    assert "operator_id" not in str(body)
    assert "内部回放原因" not in str(body)
    assert "payload" not in body["events"][1]
    assert "locked_by" not in body["events"][1]
    assert "locked_until" not in body["events"][1]


def test_outbox_admin_openapi_exposes_only_safe_event_fields(client) -> None:
    """Adding a repository field to the response model would expose operational internals."""
    schema = client.get("/openapi.json").json()["components"]["schemas"].get(
        "OutboxAdminEventResponse"
    )

    assert schema is not None
    assert set(schema["properties"]) == {
        "event_id",
        "event_type",
        "aggregate_type",
        "aggregate_id",
        "schema_version",
        "status",
        "attempt_count",
        "occurred_at",
        "published_at",
        "last_error",
    }


@pytest.mark.parametrize("reason", ("", "  ", "x", "x" * 501))
def test_replay_rejects_reason_outside_strict_bounds(
    client,
    admin_headers: dict[str, str],
    reason: str,
) -> None:
    """A blank or oversized reason would make the replay audit record unusable."""
    response = client.post(
        f"/api/v1/admin/outbox/events/{EVENT_ID}/replay",
        json={"reason": reason},
        headers=admin_headers,
    )

    assert response.status_code == 422
    if reason == "":
        assert response.json() == {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求参数校验失败",
                "detail": [
                    {
                        "type": "string_too_short",
                        "loc": ["body", "reason"],
                        "msg": "String should have at least 2 characters",
                        "input": "",
                        "ctx": {"min_length": 2},
                    }
                ],
            }
        }


def test_replay_rejects_non_uuid_event_id(client, admin_headers: dict[str, str]) -> None:
    """Passing arbitrary IDs to replay would bypass the event primary-key contract."""
    response = client.post(
        "/api/v1/admin/outbox/events/not-a-uuid/replay",
        json={"reason": "重试死信事件"},
        headers=admin_headers,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("code", "message"),
    (
        ("OUTBOX_EVENT_NOT_REPLAYABLE", "仅可回放未发布的失败或死信事件"),
        ("OUTBOX_EVENT_LEASE_ACTIVE", "Outbox 事件正在被 worker 处理，不能回放"),
    ),
)
def test_replay_returns_domain_error_envelope(
    client,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    message: str,
) -> None:
    """Converting a domain conflict to a raw server error would hide the operator action."""
    from app.core.errors import DomainError
    from app.domains.outbox import api as outbox_api

    def reject_replay(event_id: str, reason: str, admin_id: str | None) -> dict:
        raise DomainError(
            code,
            message,
            409,
            {"event_id": event_id},
        )

    monkeypatch.setattr(outbox_api, "replay_event", reject_replay)

    response = client.post(
        f"/api/v1/admin/outbox/events/{EVENT_ID}/replay",
        json={"reason": "确认后重试死信事件"},
        headers=admin_headers,
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": code,
            "message": message,
            "detail": {"event_id": EVENT_ID},
        }
    }


def test_replay_passes_reason_and_admin_id_to_service(
    client,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping the principal or reason would break the required replay audit trail."""
    from app.domains.outbox import api as outbox_api

    observed: dict[str, str | None] = {}

    def replay(event_id: str, reason: str, admin_id: str | None) -> dict:
        observed.update(event_id=event_id, reason=reason, admin_id=admin_id)
        return {
            "event_id": event_id,
            "status": "queued",
            "reason": reason,
        }

    monkeypatch.setattr(outbox_api, "replay_event", replay)

    response = client.post(
        f"/api/v1/admin/outbox/events/{EVENT_ID}/replay",
        json={"reason": "确认故障已修复后回放"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "event_id": EVENT_ID,
        "status": "queued",
        "reason": "确认故障已修复后回放",
    }
    assert observed == {
        "event_id": EVENT_ID,
        "reason": "确认故障已修复后回放",
        "admin_id": "00000000-0000-0000-0000-000000000001",
    }
    assert UUID(observed["event_id"] or "")
