"""Connection construction rules used by readiness and caching."""

import importlib

from app.core import cache
from app.db import mongo, postgres


class FakeRedis:
    pass


class FakeMongoClient:
    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    def __getitem__(self, name: str) -> object:
        return object()


class FakeCursor:
    def __init__(self) -> None:
        self.closed = False

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


def test_cache_uses_raw_redis_password_as_connection_parameter(monkeypatch) -> None:
    """Cache connections must keep special-character credentials out of the URL."""
    captured: dict = {}
    password = "pa:ss/@?#% word"

    def from_url(url: str, *args, **kwargs) -> FakeRedis:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeRedis()

    monkeypatch.setattr(cache.settings, "REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setattr(cache.settings, "REDIS_PASSWORD", password)
    monkeypatch.setattr(cache.redis.Redis, "from_url", staticmethod(from_url))

    importlib.reload(cache)

    assert captured["url"] == "redis://redis:6379/0"
    assert captured["kwargs"].get("password") == password
    assert captured["kwargs"].get("socket_connect_timeout") == 3
    assert captured["kwargs"].get("socket_timeout") == 3


def test_mongo_connections_use_three_second_selection_and_socket_timeouts(monkeypatch) -> None:
    """Readiness must not wait longer than its healthcheck envelope for MongoDB."""
    monkeypatch.setattr(mongo, "MongoClient", FakeMongoClient)
    monkeypatch.setattr(mongo, "_client", None)

    client = mongo.get_db()

    assert client is not None
    assert mongo._client.kwargs["serverSelectionTimeoutMS"] == 3000
    assert mongo._client.kwargs["connectTimeoutMS"] == 3000
    assert mongo._client.kwargs["socketTimeoutMS"] == 3000


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
