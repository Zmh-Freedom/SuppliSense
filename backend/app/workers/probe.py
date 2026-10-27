"""Internal process dependency probe for Compose worker healthchecks."""

from __future__ import annotations

import json
from typing import Any

import redis

from app.core.config import settings
from app.db.worker_migrations import worker_migration_status


def _check_redis() -> str:
    client = None
    try:
        client = redis.from_url(
            settings.REDIS_URL or "redis://localhost:6379/0",
            password=settings.REDIS_PASSWORD or None,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        client.ping()
        return "ok"
    except Exception:
        return "unavailable"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def check_worker_dependencies() -> dict[str, Any]:
    schema = worker_migration_status()
    redis_status = _check_redis()
    ready = bool(schema.get("ready")) and redis_status == "ok"
    return {
        "ready": ready,
        "checks": {"schema": schema, "redis": redis_status},
    }


def main() -> int:
    result = check_worker_dependencies()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
