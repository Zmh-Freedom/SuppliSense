"""Integration coverage for transaction-bound audit logging."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_conn, get_cursor, put_conn
from app.domains.auth.audit_repo import create_log, create_log_with_cursor


@contextmanager
def _real_connection() -> Iterator[tuple[object, object]]:
    conn = get_conn()
    cur = None
    try:
        conn.rollback()
        cur = conn.cursor()
        yield conn, cur
    finally:
        if cur is not None:
            cur.close()
        conn.rollback()
        put_conn(conn)


def _delete_audit_log(log_id: str) -> None:
    with get_cursor() as (_, cur):
        cur.execute("DELETE FROM audit_logs WHERE id = %s", (log_id,))


def test_create_log_with_cursor_stays_inside_callers_transaction():
    """A helper commit would expose the row to another connection before rollback."""
    ensure_pg_schema()
    action = f"company.merge.{uuid.uuid4()}"
    log_id: str | None = None

    try:
        with _real_connection() as (writer_conn, writer_cur):
            with _real_connection() as (observer_conn, observer_cur):
                log_id = create_log_with_cursor(
                    writer_cur,
                    action=action,
                    details={"request_id": str(uuid.uuid4())},
                )

                writer_cur.execute(
                    "SELECT action FROM audit_logs WHERE id = %s", (log_id,)
                )
                assert writer_cur.fetchone() == (action,)

                observer_cur.execute(
                    "SELECT COUNT(*) FROM audit_logs WHERE id = %s", (log_id,)
                )
                assert observer_cur.fetchone() == (0,)

                writer_conn.rollback()
                observer_conn.rollback()
                observer_cur.execute(
                    "SELECT COUNT(*) FROM audit_logs WHERE id = %s", (log_id,)
                )
                assert observer_cur.fetchone() == (0,)
    finally:
        if log_id is not None:
            _delete_audit_log(log_id)


def test_create_log_preserves_empty_details_as_sql_null():
    """Changing the wrapper to persist `{}` instead of legacy NULL must fail."""
    ensure_pg_schema()
    log_id: str | None = None

    try:
        log_id = create_log(action=f"company.merge.{uuid.uuid4()}", details={})
        with get_cursor() as (_, cur):
            cur.execute("SELECT details FROM audit_logs WHERE id = %s", (log_id,))
            assert cur.fetchone() == (None,)
    finally:
        if log_id is not None:
            _delete_audit_log(log_id)
