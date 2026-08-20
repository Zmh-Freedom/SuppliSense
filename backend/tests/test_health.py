"""Readiness probe behavior without application lifespan or real services."""

import asyncio
import threading

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app.api import health


@pytest.fixture(autouse=True)
def _disable_optional_checkpoint_check(monkeypatch):
    """Keep readiness unit tests scoped to their three explicitly mocked dependencies."""
    monkeypatch.setattr(health.settings, "AGENT_RUN_V2_ENABLED", False)


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


def test_readiness_returns_200_when_all_dependencies_are_healthy(monkeypatch) -> None:
    """Missing a dependency check must not make the probe report ready."""
    monkeypatch.setattr(health, "_check_mongo", lambda: "ok")
    monkeypatch.setattr(health.redis, "from_url", lambda *args, **kwargs: HealthyRedis())
    monkeypatch.setattr(health, "_check_postgres", lambda: "ok")

    response = _readiness_client().get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"mongo": "ok", "redis": "ok", "postgres": "ok"},
    }


def test_readiness_returns_503_when_postgres_is_unavailable(monkeypatch) -> None:
    """A failed PostgreSQL check must take the API out of service."""
    monkeypatch.setattr(health, "_check_mongo", lambda: "ok")
    monkeypatch.setattr(health.redis, "from_url", lambda *args, **kwargs: HealthyRedis())
    monkeypatch.setattr(health, "_check_postgres", lambda: "unavailable")

    response = _readiness_client().get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["postgres"] == "unavailable"


def test_readiness_returns_503_when_mongo_is_unavailable(monkeypatch) -> None:
    """A failed dedicated Mongo check must take the API out of service."""
    monkeypatch.setattr(health, "_check_mongo", lambda: "unavailable")
    monkeypatch.setattr(health.redis, "from_url", lambda *args, **kwargs: HealthyRedis())
    monkeypatch.setattr(health, "_check_postgres", lambda: "ok")

    response = _readiness_client().get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["mongo"] == "unavailable"


def test_readiness_returns_503_when_postgres_cleanup_fails(monkeypatch) -> None:
    """A PostgreSQL cleanup error must remain an unavailable readiness result."""
    class FailingCloseCursor:
        def execute(self, statement: str) -> None:
            assert statement == "SELECT 1"

        def close(self) -> None:
            raise RuntimeError("cursor close failed")

    class Connection:
        def __init__(self) -> None:
            self.closed = False

        def cursor(self) -> FailingCloseCursor:
            return FailingCloseCursor()

        def close(self) -> None:
            self.closed = True

    connection = Connection()

    class FakePsycopg:
        @staticmethod
        def connect(**kwargs) -> Connection:
            return connection

    app = FastAPI()
    app.include_router(health.router)
    monkeypatch.setattr(health, "_check_mongo", lambda: "ok")
    monkeypatch.setattr(health.redis, "from_url", lambda *args, **kwargs: HealthyRedis())
    monkeypatch.setattr(health, "psycopg2", FakePsycopg(), raising=False)

    response = TestClient(app, raise_server_exceptions=False).get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["postgres"] == "unavailable"
    assert connection.closed is True


def test_readiness_closes_redis_client_when_ping_fails(monkeypatch) -> None:
    """A Redis ping error after client creation must still release the client."""
    redis_client = FailingRedis()
    monkeypatch.setattr(health, "_check_mongo", lambda: "ok")
    monkeypatch.setattr(health.redis, "from_url", lambda *args, **kwargs: redis_client)
    monkeypatch.setattr(health, "_check_postgres", lambda: "ok")

    response = _readiness_client().get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "unavailable"
    assert redis_client.closed is True


def test_readiness_passes_raw_redis_password_outside_url(monkeypatch) -> None:
    """Redis credentials must not be URL-encoded or embedded in the endpoint."""
    captured: dict = {}
    password = "pa:ss/@?#% word"

    def from_url(url: str, *args, **kwargs) -> HealthyRedis:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return HealthyRedis()

    monkeypatch.setattr(health.settings, "REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setattr(health.settings, "REDIS_PASSWORD", password)
    monkeypatch.setattr(health.redis, "from_url", from_url)

    assert health._check_redis() == "ok"
    assert captured["url"] == "redis://redis:6379/0"
    assert captured["kwargs"].get("password") == password
    assert captured["kwargs"].get("socket_connect_timeout") == 3
    assert captured["kwargs"].get("socket_timeout") == 3


def test_readiness_starts_all_dependency_checks_concurrently(monkeypatch) -> None:
    """A sequential readiness implementation cannot release all three workers."""
    barrier = threading.Barrier(3)

    def wait_for_peers() -> str:
        barrier.wait(timeout=1)
        return "ok"

    monkeypatch.setattr(health, "_check_mongo", wait_for_peers)
    monkeypatch.setattr(health, "_check_redis", wait_for_peers)
    monkeypatch.setattr(health, "_check_postgres", wait_for_peers)

    response = _readiness_client().get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"] == {
        "mongo": "ok",
        "redis": "ok",
        "postgres": "ok",
    }


def test_readiness_keeps_event_loop_progressing_while_checks_block(monkeypatch) -> None:
    """Blocking dependency checks must run in worker threads, not the event loop."""
    all_workers_started = threading.Event()
    barrier = threading.Barrier(3, action=all_workers_started.set)
    release = threading.Event()
    progress = 0

    def blocking_check() -> str:
        barrier.wait(timeout=0.5)
        release.wait(timeout=1)
        return "ok"

    async def scenario() -> None:
        nonlocal progress
        response = Response()
        monkeypatch.setattr(health, "_check_mongo", blocking_check)
        monkeypatch.setattr(health, "_check_redis", blocking_check)
        monkeypatch.setattr(health, "_check_postgres", blocking_check)

        async def heartbeat() -> None:
            nonlocal progress
            while not release.is_set():
                progress += 1
                await asyncio.sleep(0.01)

        readiness_task = asyncio.create_task(health.readiness(response))
        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            for _ in range(30):
                if all_workers_started.is_set() and progress >= 2:
                    break
                await asyncio.sleep(0.01)
            assert all_workers_started.is_set()
            assert progress >= 2
            release.set()
            result = await readiness_task
            assert result["status"] == "ready"
        finally:
            release.set()
            await asyncio.gather(readiness_task, return_exceptions=True)
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    asyncio.run(scenario())
