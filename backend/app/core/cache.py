"""
Cache utilities with Redis.
"""

import json
import hashlib
from functools import wraps
from typing import Any, Callable

import redis

from app.core.config import settings

# Redis client for caching (db 2)
cache_client = redis.Redis(
    host=settings.MONGO_HOST if hasattr(settings, "MONGO_HOST") else "localhost",
    port=6379,
    db=2,
    decode_responses=True,
)


def cache_key(prefix: str, *args, **kwargs) -> str:
    """Generate a cache key from prefix and arguments."""
    # Hash complex arguments
    key_parts = [prefix]

    for arg in args:
        key_parts.append(str(arg))

    for k, v in sorted(kwargs.items()):
        key_parts.append(f"{k}={v}")

    key_str = ":".join(key_parts)

    # Hash if too long
    if len(key_str) > 200:
        hash_suffix = hashlib.md5(key_str.encode()).hexdigest()[:16]
        key_str = f"{prefix}:{hash_suffix}"

    return key_str


def cached(prefix: str, ttl: int = 3600):
    """
    Cache decorator for functions.

    Args:
        prefix: Cache key prefix
        ttl: Time to live in seconds (default 1 hour)

    Usage:
        @cached("company_profile", ttl=3600)
        def get_company_profile(company_name: str):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Generate cache key
            key = cache_key(prefix, *args, **kwargs)

            # Try to get from cache
            try:
                cached_value = cache_client.get(key)
                if cached_value:
                    return json.loads(cached_value)
            except Exception:
                pass  # Cache read failed, continue to function

            # Execute function
            result = func(*args, **kwargs)

            # Cache the result
            try:
                if hasattr(result, 'model_dump'):
                    value = json.dumps(result.model_dump(mode='json'), ensure_ascii=False)
                elif hasattr(result, 'dict'):
                    value = json.dumps(result.dict(), ensure_ascii=False)
                else:
                    value = json.dumps(result, default=str, ensure_ascii=False)
                cache_client.setex(key, ttl, value)
            except Exception:
                pass  # Cache write failed, continue

            return result
        return wrapper
    return decorator


def invalidate_cache(pattern: str) -> int:
    """
    Invalidate cache keys matching pattern.

    Args:
        pattern: Redis key pattern (e.g., "company:*")

    Returns:
        Number of keys deleted
    """
    try:
        keys = cache_client.keys(pattern)
        if keys:
            return cache_client.delete(*keys)
        return 0
    except Exception:
        return 0


def clear_all_cache() -> int:
    """Clear all cache."""
    try:
        return cache_client.flushdb()
    except Exception:
        return 0
