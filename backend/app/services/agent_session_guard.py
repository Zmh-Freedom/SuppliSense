"""Redis-backed single-active-run guard for interactive Agent sessions."""

from __future__ import annotations

import hashlib
import secrets
from threading import Lock

from app.core.cache import cache_client
from app.core.logging import get_logger

logger = get_logger()

_LOCK_TTL_SECONDS = 180
_LOCK_PREFIX = "agent:session-run"
_fallback_guard = Lock()
_fallback_locks: dict[str, str] = {}
_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def _lock_key(session_id: str) -> str:
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"{_LOCK_PREFIX}:{session_hash}"


def acquire_agent_session_run(session_id: str) -> str | None:
    """Acquire the session run lease, or return ``None`` when one is active."""
    token = secrets.token_urlsafe(24)
    try:
        acquired = cache_client.set(
            _lock_key(session_id), token, nx=True, ex=_LOCK_TTL_SECONDS
        )
    except Exception as exc:
        # Unit tests and a degraded local developer environment may not have
        # Redis. Keep a process-local guard in that case; production still
        # uses the distributed Redis lease above.
        logger.warning("agent_session_guard_fallback", error=str(exc))
        with _fallback_guard:
            key = _lock_key(session_id)
            if key in _fallback_locks:
                return None
            _fallback_locks[key] = token
            return f"memory:{token}"
    return token if acquired else None


def renew_agent_session_run(session_id: str, token: str) -> bool:
    """Extend a lease only when it is still owned by this stream."""
    if token.startswith("memory:"):
        with _fallback_guard:
            return _fallback_locks.get(_lock_key(session_id)) == token.removeprefix("memory:")
    try:
        return bool(
            cache_client.eval(
                _RENEW_SCRIPT, 1, _lock_key(session_id), token, _LOCK_TTL_SECONDS
            )
        )
    except Exception as exc:
        logger.warning("agent_session_guard_renew_failed", error=str(exc))
        return False


def release_agent_session_run(session_id: str, token: str) -> None:
    """Release a lease without deleting a newer run's lock."""
    if token.startswith("memory:"):
        with _fallback_guard:
            key = _lock_key(session_id)
            if _fallback_locks.get(key) == token.removeprefix("memory:"):
                _fallback_locks.pop(key, None)
        return
    try:
        cache_client.eval(_RELEASE_SCRIPT, 1, _lock_key(session_id), token)
    except Exception as exc:
        logger.warning("agent_session_guard_release_failed", error=str(exc))
