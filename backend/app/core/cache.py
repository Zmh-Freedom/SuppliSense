"""
Cache utilities with Redis.
"""

import json
import hashlib
from functools import wraps
from typing import Any, Callable

import redis

from app.core.config import settings

_ALLOWED_MODELS: dict[str, str] = {
    "app.schemas.company.CompanyProfile": "app.schemas.company:CompanyProfile",
    "app.schemas.risk.RiskInfo": "app.schemas.risk:RiskInfo",
}

# Redis client for caching (db 2)
cache_client = redis.Redis.from_url(
    settings.REDIS_URL,
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
            key = cache_key(prefix, *args, **kwargs)

            try:
                cached_value = cache_client.get(key)
                if cached_value:
                    data = json.loads(cached_value)
                    # Reconstruct Pydantic model if type info stored
                    if isinstance(data, dict) and '__type__' in data:
                        type_name = data['__type__']
                        if type_name in _ALLOWED_MODELS:
                            mod_name, cls_name = _ALLOWED_MODELS[type_name].rsplit(':', 1)
                            import importlib
                            mod = importlib.import_module(mod_name)
                            cls = getattr(mod, cls_name)
                            return cls(**data['data'])
                        return data['data'] if 'data' in data else data
                    return data
            except Exception:
                pass

            result = func(*args, **kwargs)

            try:
                if hasattr(result, 'model_dump'):
                    data = result.model_dump(mode='json')
                    type_name = type(result).__module__ + '.' + type(result).__qualname__
                    value = json.dumps({'__type__': type_name, 'data': data}, ensure_ascii=False)
                else:
                    value = json.dumps(result, default=str, ensure_ascii=False)
                cache_client.setex(key, ttl, value)
            except Exception:
                pass

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
