"""Paid refresh budget must fail closed across worker processes."""

import hashlib
import uuid
from datetime import datetime, timezone

import pytest

from app.core import paid_quota


def test_paid_quota_rejects_exhausted_budget(monkeypatch) -> None:
    monkeypatch.setattr(paid_quota.cache_client, "eval", lambda *_args: 0)

    with pytest.raises(paid_quota.PaidQuotaExceeded):
        paid_quota.reserve_paid_refresh("user-1", 1)


def test_paid_quota_rejects_unavailable_redis(monkeypatch) -> None:
    monkeypatch.setattr(
        paid_quota.cache_client,
        "eval",
        lambda *_args: (_ for _ in ()).throw(ConnectionError("redis unavailable")),
    )

    with pytest.raises(paid_quota.PaidQuotaUnavailable):
        paid_quota.reserve_paid_refresh("user-1", 1)


def test_paid_quota_reserves_global_and_user_budget_atomically(monkeypatch) -> None:
    observed: list[tuple] = []
    monkeypatch.setattr(
        paid_quota.cache_client,
        "eval",
        lambda *args: observed.append(args) or 1,
    )

    paid_quota.reserve_paid_refresh("user-1", 3)

    assert observed[0][1] == 2
    assert observed[0][4] == 3
    assert observed[0][2].endswith(":global")
    assert ":user:" in observed[0][3]


@pytest.mark.integration
def test_paid_quota_redis_enforces_daily_user_limit(monkeypatch) -> None:
    user_id = str(uuid.uuid4())
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    user_key = f"paid_refresh:{day}:user:{user_hash}"
    monkeypatch.setattr(paid_quota.settings, "PAID_REFRESH_USER_DAILY_LIMIT", 1)
    monkeypatch.setattr(paid_quota.settings, "PAID_REFRESH_GLOBAL_DAILY_LIMIT", 100000)

    try:
        paid_quota.reserve_paid_refresh(user_id, 1)
        with pytest.raises(paid_quota.PaidQuotaExceeded):
            paid_quota.reserve_paid_refresh(user_id, 1)
    finally:
        paid_quota.cache_client.delete(user_key)
