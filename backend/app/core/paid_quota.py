"""Shared daily budget for paid supplier refresh operations."""

import hashlib
from datetime import datetime, timedelta, timezone

from app.core.cache import cache_client
from app.core.config import settings


class PaidQuotaExceeded(ValueError):
    """The requested refresh would exceed today's paid-operation budget."""


class PaidQuotaUnavailable(RuntimeError):
    """The shared budget store is unavailable; paid work must stop."""


_RESERVE_SCRIPT = """
local cost = tonumber(ARGV[1])
local global_limit = tonumber(ARGV[2])
local user_limit = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local global_count = tonumber(redis.call('GET', KEYS[1]) or '0')
local user_count = tonumber(redis.call('GET', KEYS[2]) or '0')
if global_count + cost > global_limit or user_count + cost > user_limit then
    return 0
end
redis.call('INCRBY', KEYS[1], cost)
redis.call('INCRBY', KEYS[2], cost)
if redis.call('TTL', KEYS[1]) < 0 then redis.call('EXPIRE', KEYS[1], ttl) end
if redis.call('TTL', KEYS[2]) < 0 then redis.call('EXPIRE', KEYS[2], ttl) end
return 1
"""


def reserve_paid_refresh(user_id: str, cost: int = 1) -> None:
    """Atomically reserve one budget unit per planned company refresh."""
    if not user_id or cost < 1:
        raise ValueError("付费刷新额度参数无效")
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y%m%d")
    next_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    ttl = max(1, int((next_day - now).total_seconds()))
    user_key = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    try:
        reserved = cache_client.eval(
            _RESERVE_SCRIPT,
            2,
            f"paid_refresh:{day}:global",
            f"paid_refresh:{day}:user:{user_key}",
            cost,
            settings.PAID_REFRESH_GLOBAL_DAILY_LIMIT,
            settings.PAID_REFRESH_USER_DAILY_LIMIT,
            ttl,
        )
    except Exception as exc:
        raise PaidQuotaUnavailable("付费刷新额度服务暂不可用") from exc
    if reserved != 1:
        raise PaidQuotaExceeded("今日付费刷新额度已用尽")


_RESERVE_ONCE_SCRIPT = """
local cost = tonumber(ARGV[1])
local global_limit = tonumber(ARGV[2])
local user_limit = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
if redis.call('EXISTS', KEYS[3]) == 1 then
    return 2
end
local global_count = tonumber(redis.call('GET', KEYS[1]) or '0')
local user_count = tonumber(redis.call('GET', KEYS[2]) or '0')
if global_count + cost > global_limit or user_count + cost > user_limit then
    return 0
end
redis.call('INCRBY', KEYS[1], cost)
redis.call('INCRBY', KEYS[2], cost)
if redis.call('TTL', KEYS[1]) < 0 then redis.call('EXPIRE', KEYS[1], ttl) end
if redis.call('TTL', KEYS[2]) < 0 then redis.call('EXPIRE', KEYS[2], ttl) end
redis.call('SET', KEYS[3], '1', 'EX', 604800)
return 1
"""


def reserve_paid_refresh_once(
    user_id: str, reservation_key: str, cost: int = 1
) -> bool:
    """Reserve budget once per durable job.

    Returns True when this call reserved the budget and False when the same
    reservation already exists, so a retry never charges the user twice.
    """
    if not user_id or not reservation_key or cost < 1:
        raise ValueError("付费刷新额度参数无效")
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y%m%d")
    next_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    ttl = max(1, int((next_day - now).total_seconds()))
    user_key = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    job_key = hashlib.sha256(reservation_key.encode("utf-8")).hexdigest()
    try:
        reserved = cache_client.eval(
            _RESERVE_ONCE_SCRIPT,
            3,
            f"paid_refresh:{day}:global",
            f"paid_refresh:{day}:user:{user_key}",
            f"paid_refresh:reservation:{job_key}",
            cost,
            settings.PAID_REFRESH_GLOBAL_DAILY_LIMIT,
            settings.PAID_REFRESH_USER_DAILY_LIMIT,
            ttl,
        )
    except Exception as exc:
        raise PaidQuotaUnavailable("付费刷新额度服务暂不可用") from exc
    if reserved == 0:
        raise PaidQuotaExceeded("今日付费刷新额度已用尽")
    return reserved == 1
