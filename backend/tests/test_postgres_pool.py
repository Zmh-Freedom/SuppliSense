from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import threading

import pytest
from psycopg2.pool import PoolError

from app.db import postgres


class _Cursor:
    def execute(self, _query: str) -> None:
        pass

    def close(self) -> None:
        pass


class _Connection:
    def cursor(self) -> _Cursor:
        return _Cursor()


class _SingleConnectionPool:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._checked_out: set[int] = set()

    def getconn(self) -> _Connection:
        with self._lock:
            if self._checked_out:
                raise PoolError("connection pool exhausted")
            connection = _Connection()
            self._checked_out.add(id(connection))
            return connection

    def putconn(self, connection: _Connection, close: bool = False) -> None:
        del close
        with self._lock:
            self._checked_out.remove(id(connection))


def test_get_conn_waits_for_a_checked_out_connection_to_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _SingleConnectionPool()
    monkeypatch.setattr(postgres, "_get_pool", lambda: pool)
    monkeypatch.setattr(postgres, "_pool", pool)
    monkeypatch.setattr(
        postgres,
        "_pool_slots",
        threading.BoundedSemaphore(1),
        raising=False,
    )

    first_connection = postgres.get_conn()
    started = threading.Event()

    def borrow_connection() -> bool:
        started.set()
        connection = postgres.get_conn()
        postgres.put_conn(connection)
        return True

    with ThreadPoolExecutor(max_workers=1) as executor:
        waiting = executor.submit(borrow_connection)
        assert started.wait(timeout=1)
        with pytest.raises(TimeoutError):
            waiting.result(timeout=0.1)

        postgres.put_conn(first_connection)
        assert waiting.result(timeout=1) is True
