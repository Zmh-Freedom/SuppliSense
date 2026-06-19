"""
PostgreSQL connection pool management (psycopg2, synchronous).
"""

import logging
from contextlib import contextmanager

import psycopg2
from psycopg2.extensions import connection as PgConnection
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
            connect_timeout=10,
        )
    return _pool


def get_conn() -> PgConnection:
    return _get_pool().getconn()


def put_conn(conn: PgConnection) -> None:
    _get_pool().putconn(conn)


@contextmanager
def get_cursor():
    conn = get_conn()
    try:
        cur = conn.cursor()
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("pg_pool_closed")
