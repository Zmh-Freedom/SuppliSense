"""
Health check endpoints for Kubernetes probes.
"""

import redis
from fastapi import APIRouter

from app.core.config import settings
from app.db.mongo import get_db

router = APIRouter(tags=["health"])


@router.get("/health/ready")
async def readiness():
    """Kubernetes readiness probe - checks if app is ready to serve."""
    checks = {"mongo": "ok", "redis": "ok"}
    healthy = True

    # Check MongoDB
    try:
        db = get_db()
        db.command("ping")
    except Exception:
        checks["mongo"] = "unavailable"
        healthy = False

    # Check Redis
    try:
        r = redis.from_url(settings.REDIS_URL or "redis://localhost:6379")
        r.ping()
        r.close()
    except Exception:
        checks["redis"] = "unavailable"
        healthy = False

    return {"status": "ready" if healthy else "not_ready", "checks": checks}


@router.get("/health/live")
async def liveness():
    """Kubernetes liveness probe - just checks if process is alive."""
    return {"status": "alive"}
