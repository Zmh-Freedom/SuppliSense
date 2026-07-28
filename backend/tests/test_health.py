"""Readiness probe behavior without application lifespan or real services."""

from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import health


class HealthyMongo:
    def command(self, command: str) -> None:
        assert command == "ping"


class HealthyRedis:
    def ping(self) -> None:
        return None

    def close(self) -> None:
        return None


class FailingRedis:
    def __init__(self) -> None:
        self.closed = False

    def ping(self) -> None:
        raise RuntimeError("Redis unavailable")

    def close(self) -> None:
        self.closed = True


class HealthyCursor:
    def execute(self, statement: str) -> None:
        assert statement == "SELECT 1"


def _readiness_client() -> TestClient:
    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app)


@contextmanager
def _healthy_pg_cursor():
    yield None, HealthyCursor()


@contextmanager
def _failing_pg_cursor():
    raise RuntimeError("PostgreSQL unavailable")
    yield


def test_readiness_returns_200_when_all_dependencies_are_healthy(monkeypatch) -> None:
    """Missing a dependency check must not make the probe report ready."""
    monkeypatch.setattr(health, "get_db", lambda: HealthyMongo())
    monkeypatch.setattr(health.redis, "from_url", lambda _: HealthyRedis())
    monkeypatch.setattr(health, "get_cursor", _healthy_pg_cursor, raising=False)

    response = _readiness_client().get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"mongo": "ok", "redis": "ok", "postgres": "ok"},
    }


def test_readiness_returns_503_when_postgres_is_unavailable(monkeypatch) -> None:
    """A failed PostgreSQL check must take the API out of service."""
    monkeypatch.setattr(health, "get_db", lambda: HealthyMongo())
    monkeypatch.setattr(health.redis, "from_url", lambda _: HealthyRedis())
    monkeypatch.setattr(health, "get_cursor", _failing_pg_cursor, raising=False)

    response = _readiness_client().get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["postgres"] == "unavailable"


def test_readiness_closes_redis_client_when_ping_fails(monkeypatch) -> None:
    """A Redis ping error after client creation must still release the client."""
    redis_client = FailingRedis()
    monkeypatch.setattr(health, "get_db", lambda: HealthyMongo())
    monkeypatch.setattr(health.redis, "from_url", lambda _: redis_client)
    monkeypatch.setattr(health, "get_cursor", _healthy_pg_cursor, raising=False)

    response = _readiness_client().get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "unavailable"
    assert redis_client.closed is True
