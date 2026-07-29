"""
PostgreSQL connection pool management (psycopg2, synchronous).
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg2
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor
from psycopg2.pool import ThreadedConnectionPool

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: ThreadedConnectionPool | None = None


def _get_pool() -> ThreadedConnectionPool:
    global _pool
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
    return _pool


def get_conn() -> PgConnection:
    pool = _get_pool()
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


def put_conn(conn: PgConnection, *, close: bool = False) -> None:
    _get_pool().putconn(conn, close=close)


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
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("pg_pool_closed")
