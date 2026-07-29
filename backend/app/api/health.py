"""Health check endpoints for Kubernetes probes."""

import asyncio

import psycopg2
import redis
from fastapi import APIRouter, Response, status

from app.core.config import settings
from app.db.mongo import get_db

router = APIRouter(tags=["health"])


def _check_mongo() -> str:
    try:
        db = get_db()
        db.command("ping")
    except Exception:
        return "unavailable"
    return "ok"


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
    except Exception:
        return "unavailable"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return "ok"


def _check_postgres() -> str:
    connection = None
    cursor = None
    cleanup_failed = False
    try:
        connection = psycopg2.connect(
            host=settings.PG_HOST,
            port=settings.PG_PORT,
            user=settings.PG_USER,
            password=settings.PG_PASSWORD,
            dbname=settings.PG_DB,
            connect_timeout=3,
            options="-c statement_timeout=3000",
        )
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
    except Exception:
        return "unavailable"
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                cleanup_failed = True
        if connection is not None:
            try:
                connection.close()
            except Exception:
                cleanup_failed = True
    if cleanup_failed:
        return "unavailable"
    return "ok"


@router.get("/health/ready")
async def readiness(response: Response) -> dict:
    """Kubernetes readiness probe - checks if app is ready to serve."""
    mongo, redis_check, postgres = await asyncio.gather(
        asyncio.to_thread(_check_mongo),
        asyncio.to_thread(_check_redis),
        asyncio.to_thread(_check_postgres),
    )
    checks = {"mongo": mongo, "redis": redis_check, "postgres": postgres}
    healthy = all(check == "ok" for check in checks.values())

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ready" if healthy else "not_ready", "checks": checks}


@router.get("/health/live")
async def liveness() -> dict:
    """Kubernetes liveness probe - just checks if process is alive."""
    return {"status": "alive"}
