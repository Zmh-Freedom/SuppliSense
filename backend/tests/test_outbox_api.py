"""Contract tests for the administrator transactional-Outbox API."""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.core.security import create_access_token
from app.schemas.user import UserInDB


EVENT_ID = "00000000-0000-4000-8000-000000000901"


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


def test_outbox_events_requires_admin(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing the role guard would expose operational event payloads to analysts."""
    token = _analyst_token(monkeypatch)

    response = client.get(
        "/api/v1/admin/outbox/events",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_outbox_events_rejects_unknown_status(client, admin_headers: dict[str, str]) -> None:
    """Accepting arbitrary status values could produce an accidental unbounded query."""
    response = client.get(
        "/api/v1/admin/outbox/events?status=published",
        headers=admin_headers,
    )

    assert response.status_code == 422


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
                "payload": {"company_id": "00000000-0000-4000-8000-000000000904"},
                "occurred_at": "2026-08-08T00:00:02+00:00",
                "published_at": None,
                "attempt_count": 1,
                "last_error": "temporary failure",
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
                "payload": {"company_id": "00000000-0000-4000-8000-000000000902"},
                "occurred_at": "2026-08-08T00:00:01Z",
                "published_at": None,
                "attempt_count": 0,
                "last_error": None,
                "next_attempt_at": "2026-08-08T00:00:01Z",
                "locked_by": None,
                "locked_until": None,
                "dead_lettered_at": None,
            },
            {
                "event_id": "00000000-0000-4000-8000-000000000903",
                "event_type": "company.updated",
                "aggregate_type": "company",
                "aggregate_id": "00000000-0000-4000-8000-000000000904",
                "schema_version": 1,
                "payload": {"company_id": "00000000-0000-4000-8000-000000000904"},
                "occurred_at": "2026-08-08T00:00:02Z",
                "published_at": None,
                "attempt_count": 1,
                "last_error": "temporary failure",
                "next_attempt_at": "2026-08-08T00:00:04Z",
                "locked_by": None,
                "locked_until": None,
                "dead_lettered_at": None,
            },
        ]
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


def test_replay_rejects_non_uuid_event_id(client, admin_headers: dict[str, str]) -> None:
    """Passing arbitrary IDs to replay would bypass the event primary-key contract."""
    response = client.post(
        "/api/v1/admin/outbox/events/not-a-uuid/replay",
        json={"reason": "重试死信事件"},
        headers=admin_headers,
    )

    assert response.status_code == 422


def test_replay_returns_domain_error_envelope_for_ineligible_event(
    client,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Converting a domain conflict to a raw server error would hide the operator action."""
    from app.core.errors import DomainError
    from app.domains.outbox import api as outbox_api

    def reject_replay(event_id: str, reason: str, admin_id: str | None) -> dict:
        raise DomainError(
            "OUTBOX_EVENT_NOT_REPLAYABLE",
            "仅可回放未发布的失败或死信事件",
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
            "code": "OUTBOX_EVENT_NOT_REPLAYABLE",
            "message": "仅可回放未发布的失败或死信事件",
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
