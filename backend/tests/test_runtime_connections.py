"""Connection construction rules used by readiness and caching."""

import importlib
from pathlib import Path

import pytest

from app.api import health
from app.core import cache
from app.db import mongo, postgres

REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeMongoClient:
    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs
        self.closed = False

    def __getitem__(self, name: str) -> object:
        return object()

    def close(self) -> None:
        self.closed = True


class FakeMongoDatabase:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def command(self, command: str) -> None:
        assert command == "ping"
        if self.error is not None:
            raise self.error


class FakeReadinessMongoClient(FakeMongoClient):
    def __init__(
        self,
        database: FakeMongoDatabase,
        *,
        close_error: Exception | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.database = database
        self.close_error = close_error

    def __getitem__(self, name: str) -> FakeMongoDatabase:
        return self.database

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeCursor:
    def __init__(self) -> None:
        self.closed = False

    def execute(self, statement: str) -> None:
        assert statement == "SELECT 1"

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor_instance = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_cache_uses_db_two_with_shared_docker_redis_url(monkeypatch) -> None:
    """An explicit DB in the shared URL must not redirect cache flushes to DB 0."""
    password = "pa:ss/@?#% word"
    redis_url = next(
        line.split("=", 1)[1]
        for line in (REPO_ROOT / ".env.docker.example").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.startswith("REDIS_URL=")
    )
    connection_kwargs: dict = {}

    try:
        monkeypatch.setattr(cache.settings, "REDIS_URL", redis_url)
        monkeypatch.setattr(cache.settings, "REDIS_PASSWORD", password)
        importlib.reload(cache)
        connection_kwargs = cache.cache_client.connection_pool.connection_kwargs
    finally:
        monkeypatch.undo()
        importlib.reload(cache)

    assert isinstance(cache.cache_client, cache.redis.Redis)
    assert connection_kwargs["db"] == 2
    assert connection_kwargs["password"] == password
    assert connection_kwargs["socket_connect_timeout"] == 3
    assert connection_kwargs["socket_timeout"] == 3


def test_application_mongo_clients_keep_pre_readiness_timeout_behavior(monkeypatch) -> None:
    """Readiness bounds must not shorten sync or async business operations."""
    monkeypatch.setattr(mongo, "MongoClient", FakeMongoClient)
    monkeypatch.setattr(mongo, "AsyncMongoClient", FakeMongoClient)
    monkeypatch.setattr(mongo, "_client", None)
    monkeypatch.setattr(mongo, "_async_client", None)

    mongo.get_db()
    mongo.get_async_db()

    for client in (mongo._client, mongo._async_client):
        assert client.kwargs["serverSelectionTimeoutMS"] == 5000
        assert "connectTimeoutMS" not in client.kwargs
        assert "socketTimeoutMS" not in client.kwargs


def test_readiness_mongo_uses_bounded_dedicated_client_and_closes_it(
    monkeypatch,
) -> None:
    """Readiness must not borrow or mutate the shared application client."""
    client = FakeReadinessMongoClient(FakeMongoDatabase())

    def create_client(**kwargs) -> FakeReadinessMongoClient:
        client.kwargs = kwargs
        return client

    monkeypatch.setattr(health, "MongoClient", create_client, raising=False)
    monkeypatch.setattr(
        health,
        "get_db",
        lambda: (_ for _ in ()).throw(AssertionError("shared client used")),
        raising=False,
    )

    assert health._check_mongo() == "ok"
    assert client.closed is True
    assert client.kwargs["serverSelectionTimeoutMS"] == 3000
    assert client.kwargs["connectTimeoutMS"] == 3000
    assert client.kwargs["socketTimeoutMS"] == 3000


def test_readiness_mongo_closes_dedicated_client_when_ping_fails(monkeypatch) -> None:
    """A failed ping must return unavailable and release the readiness client."""
    client = FakeReadinessMongoClient(
        FakeMongoDatabase(RuntimeError("ping failed"))
    )
    monkeypatch.setattr(health, "MongoClient", lambda **kwargs: client, raising=False)
    monkeypatch.setattr(
        health,
        "get_db",
        lambda: (_ for _ in ()).throw(AssertionError("shared client used")),
        raising=False,
    )

    assert health._check_mongo() == "unavailable"
    assert client.closed is True


def test_readiness_mongo_returns_unavailable_when_client_close_fails(
    monkeypatch,
) -> None:
    """A readiness client cleanup failure must not escape or report healthy."""
    client = FakeReadinessMongoClient(
        FakeMongoDatabase(),
        close_error=RuntimeError("close failed"),
    )
    monkeypatch.setattr(health, "MongoClient", lambda **kwargs: client, raising=False)
    monkeypatch.setattr(
        health,
        "get_db",
        lambda: (_ for _ in ()).throw(AssertionError("shared client used")),
        raising=False,
    )

    assert health._check_mongo() == "unavailable"
    assert client.closed is True


def test_postgres_pool_uses_three_second_connect_timeout(monkeypatch) -> None:
    """A new PostgreSQL pool must bound its connection attempts to three seconds."""
    captured: dict = {}

    class FakePool:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(postgres, "ThreadedConnectionPool", FakePool)
    monkeypatch.setattr(postgres, "_pool", None)

    postgres._get_pool()

    assert captured["connect_timeout"] == 3


def test_get_cursor_closes_cursor_before_returning_connection(monkeypatch) -> None:
    """Cursors created by the shared context manager must be closed deterministically."""
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    returned: list[FakeConnection] = []
    monkeypatch.setattr(postgres, "get_conn", lambda: connection)
    monkeypatch.setattr(postgres, "put_conn", returned.append)

    with postgres.get_cursor() as (_, active_cursor):
        assert active_cursor is cursor

    assert cursor.closed is True
    assert returned == [connection]


def test_get_cursor_discards_connection_when_cursor_close_fails(monkeypatch) -> None:
    """A cursor cleanup failure must not strand its pooled connection."""
    class FailingCloseCursor(FakeCursor):
        def close(self) -> None:
            raise RuntimeError("cursor close failed")

    cursor = FailingCloseCursor()
    connection = FakeConnection(cursor)
    returned: list[tuple[FakeConnection, bool]] = []

    def return_connection(conn: FakeConnection, *, close: bool = False) -> None:
        returned.append((conn, close))

    monkeypatch.setattr(postgres, "get_conn", lambda: connection)
    monkeypatch.setattr(postgres, "put_conn", return_connection)

    with pytest.raises(RuntimeError, match="cursor close failed"):
        with postgres.get_cursor():
            pass

    assert returned == [(connection, True)]


def test_get_cursor_preserves_body_error_when_all_cleanup_fails(monkeypatch) -> None:
    """Cursor, rollback, and pool cleanup errors must not replace the body error."""
    class FailingCloseCursor(FakeCursor):
        def close(self) -> None:
            raise RuntimeError("cursor close failed")

    class FailingRollbackConnection(FakeConnection):
        def rollback(self) -> None:
            self.rolled_back = True
            raise RuntimeError("rollback failed")

    cursor = FailingCloseCursor()
    connection = FailingRollbackConnection(cursor)
    returned: list[tuple[FakeConnection, bool]] = []

    def fail_return(conn: FakeConnection, *, close: bool = False) -> None:
        returned.append((conn, close))
        raise RuntimeError("pool return failed")

    monkeypatch.setattr(postgres, "get_conn", lambda: connection)
    monkeypatch.setattr(postgres, "put_conn", fail_return)

    with pytest.raises(ValueError, match="body failed"):
        with postgres.get_cursor():
            raise ValueError("body failed")

    assert connection.rolled_back is True
    assert returned == [(connection, True)]


def test_readiness_postgres_uses_one_bounded_connection_and_closes_resources(monkeypatch) -> None:
    """Readiness must avoid initializing the multi-connection application pool."""
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    connection.closed = False
    connect_calls: list[dict] = []

    class FakePsycopg:
        @staticmethod
        def connect(**kwargs) -> FakeConnection:
            connect_calls.append(kwargs)
            return connection

    monkeypatch.setattr(health, "psycopg2", FakePsycopg(), raising=False)

    assert health._check_postgres() == "ok"
    assert len(connect_calls) == 1
    assert connect_calls[0]["connect_timeout"] == 3
    assert connect_calls[0]["options"] == "-c statement_timeout=3000"
    assert cursor.closed is True
    assert connection.closed is True


def test_readiness_postgres_closes_resources_when_select_fails(monkeypatch) -> None:
    """A failed readiness query must still close its dedicated connection."""
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    connection.closed = False

    def fail_execute(statement: str) -> None:
        assert statement == "SELECT 1"
        raise RuntimeError("query failed")

    cursor.execute = fail_execute

    class FakePsycopg:
        @staticmethod
        def connect(**kwargs) -> FakeConnection:
            return connection

    monkeypatch.setattr(health, "psycopg2", FakePsycopg(), raising=False)

    assert health._check_postgres() == "unavailable"
    assert cursor.closed is True
    assert connection.closed is True


def test_readiness_postgres_returns_unavailable_when_both_cleanup_calls_fail(monkeypatch) -> None:
    """Cleanup failures must not skip connection close or escape the readiness probe."""
    class FailingCloseCursor(FakeCursor):
        def __init__(self) -> None:
            super().__init__()
            self.close_attempted = False

        def close(self) -> None:
            self.close_attempted = True
            raise RuntimeError("cursor close failed")

    class FailingCloseConnection(FakeConnection):
        def __init__(self, cursor: FakeCursor) -> None:
            super().__init__(cursor)
            self.close_attempted = False

        def close(self) -> None:
            self.close_attempted = True
            raise RuntimeError("connection close failed")

    cursor = FailingCloseCursor()
    connection = FailingCloseConnection(cursor)

    class FakePsycopg:
        @staticmethod
        def connect(**kwargs) -> FailingCloseConnection:
            return connection

    monkeypatch.setattr(health, "psycopg2", FakePsycopg(), raising=False)

    try:
        result = health._check_postgres()
    except Exception:
        result = "raised"

    assert result == "unavailable"
    assert cursor.close_attempted is True
    assert connection.close_attempted is True
