"""PostgreSQL advisory-lock leadership for the in-process scheduler."""

from app.db.postgres import PgConnection, get_conn, put_conn


SCHEDULER_ADVISORY_LOCK_KEY = 914_623_017


class SchedulerLeadership:
    """Hold a session-scoped advisory lock for exactly one scheduler owner."""

    def __init__(self) -> None:
        self._connection: PgConnection | None = None

    def acquire(self) -> bool:
        if self._connection is not None:
            return True
        connection = get_conn()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", (SCHEDULER_ADVISORY_LOCK_KEY,))
                acquired = bool(cursor.fetchone()[0])
            connection.commit()
        except Exception:
            put_conn(connection, close=True)
            raise
        if not acquired:
            put_conn(connection)
            return False
        self._connection = connection
        return True

    def release(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (SCHEDULER_ADVISORY_LOCK_KEY,))
            connection.commit()
        finally:
            put_conn(connection)
