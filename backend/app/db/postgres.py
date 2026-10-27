"""
PostgreSQL connection pool management (psycopg2, synchronous).
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock

import psycopg2
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor
from psycopg2.pool import PoolError, ThreadedConnectionPool

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: ThreadedConnectionPool | None = None
_pool_slots: BoundedSemaphore | None = None
_pool_lock = Lock()


def _get_pool() -> ThreadedConnectionPool:
    global _pool, _pool_slots
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(
                    minconn=settings.PG_POOL_MIN,
                    maxconn=settings.PG_POOL_MAX,
                    host=settings.PG_HOST,
                    port=settings.PG_PORT,
                    user=settings.PG_USER,
                    password=settings.PG_PASSWORD,
                    dbname=settings.PG_DB,
                    connect_timeout=3,
                )
                _pool_slots = BoundedSemaphore(settings.PG_POOL_MAX)
    return _pool


def get_conn() -> PgConnection:
    pool = _get_pool()
    slots = _pool_slots
    if slots is None:
        raise RuntimeError("PostgreSQL connection pool slots are not initialized")
    if not slots.acquire(timeout=settings.PG_POOL_ACQUIRE_TIMEOUT_SECONDS):
        raise PoolError("timed out waiting for a PostgreSQL connection")
    try:
        conn = pool.getconn()
        # 健康检查：跳过死连接
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
        except Exception:
            # 连接已死，关闭并从池中获取新连接
            try:
                pool.putconn(conn, close=True)
            except Exception:
                pass
            conn = pool.getconn()
        return conn
    except BaseException:
        slots.release()
        raise


def put_conn(conn: PgConnection, *, close: bool = False) -> None:
    try:
        _get_pool().putconn(conn, close=close)
    finally:
        slots = _pool_slots
        if slots is not None:
            slots.release()


@contextmanager
def get_cursor() -> Iterator[tuple[PgConnection, PgCursor]]:
    conn = get_conn()
    cur = None
    original_error: BaseException | None = None
    discard_connection = False
    try:
        cur = conn.cursor()
        yield conn, cur
        conn.commit()
    except BaseException as exc:
        original_error = exc
        try:
            conn.rollback()
        except Exception:
            discard_connection = True
        raise
    finally:
        cleanup_error: Exception | None = None
        if cur is not None:
            try:
                cur.close()
            except Exception as exc:
                cleanup_error = exc
                discard_connection = True
        try:
            if discard_connection:
                put_conn(conn, close=True)
            else:
                put_conn(conn)
        except Exception as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if original_error is None and cleanup_error is not None:
            raise cleanup_error


def close_pool() -> None:
    global _pool, _pool_slots
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
            _pool_slots = None
        logger.info("pg_pool_closed")
