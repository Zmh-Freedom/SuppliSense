"""PostgreSQL data access for transactional Outbox events."""

from typing import Any
from uuid import uuid4

from psycopg2.extras import Json

from app.db.postgres import PgCursor, get_cursor
from app.domains.outbox.sanitization import sanitize_delivery_error


def enqueue_event(
    cur: PgCursor,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict,
    schema_version: int = 1,
) -> str:
    """Insert an event using the caller-owned transaction."""
    event_id = str(uuid4())
    cur.execute(
        """
        INSERT INTO outbox_events (
            event_id, event_type, aggregate_type, aggregate_id, schema_version, payload
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            event_id,
            event_type,
            aggregate_type,
            aggregate_id,
            schema_version,
            Json(payload),
        ),
    )
    return event_id


def claim_events(worker_id: str, batch_size: int, lease_seconds: int) -> list[dict]:
    """Lease due events in one short transaction without blocking other workers."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT * FROM outbox_events
            WHERE published_at IS NULL
              AND dead_lettered_at IS NULL
              AND next_attempt_at <= NOW()
              AND (locked_until IS NULL OR locked_until < NOW())
            ORDER BY next_attempt_at, occurred_at
            FOR UPDATE SKIP LOCKED
            LIMIT %s
            """,
            (batch_size,),
        )
        events = _rows_to_dicts(cur)
        if not events:
            return []

        event_ids = [event["event_id"] for event in events]
        cur.execute(
            """
            UPDATE outbox_events
            SET locked_by = %s,
                locked_until = NOW() + (%s * INTERVAL '1 second')
            WHERE event_id = ANY(%s::uuid[])
            """,
            (worker_id, lease_seconds, event_ids),
        )
        for event in events:
            event["locked_by"] = worker_id
        return events


def get_pending_stats() -> dict:
    """Return the current unpublished backlog size and its oldest event age."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT
                COUNT(*),
                EXTRACT(EPOCH FROM NOW() - MIN(occurred_at))
            FROM outbox_events
            WHERE published_at IS NULL AND dead_lettered_at IS NULL
            """
        )
        pending, oldest_age_seconds = cur.fetchone()
    return {
        "pending": int(pending),
        "oldest_age_seconds": (
            float(oldest_age_seconds) if oldest_age_seconds is not None else None
        ),
    }


def is_consumed(event_id: str, consumer_name: str) -> bool:
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT 1 FROM outbox_consumptions
            WHERE event_id = %s AND consumer_name = %s
            """,
            (event_id, consumer_name),
        )
        return cur.fetchone() is not None


def record_consumption(event_id: str, consumer_name: str) -> bool:
    with get_cursor() as (_, cur):
        cur.execute(
            """
            INSERT INTO outbox_consumptions (event_id, consumer_name)
            VALUES (%s, %s)
            ON CONFLICT (event_id, consumer_name) DO NOTHING
            RETURNING event_id
            """,
            (event_id, consumer_name),
        )
        return cur.fetchone() is not None


def mark_published(event_id: str, worker_id: str) -> bool:
    with get_cursor() as (_, cur):
        cur.execute(
            """
            UPDATE outbox_events
            SET published_at = NOW(), locked_by = NULL, locked_until = NULL
            WHERE event_id = %s
              AND locked_by = %s
              AND locked_until >= NOW()
              AND published_at IS NULL
              AND dead_lettered_at IS NULL
            RETURNING event_id
            """,
            (event_id, worker_id),
        )
        return cur.fetchone() is not None


def mark_failed(
    event_id: str,
    error: str,
    max_attempts: int,
    retry_seconds: int,
    worker_id: str,
) -> bool:
    """Record one failed attempt and either schedule it or dead-letter it."""
    safe_error = sanitize_delivery_error(error)
    with get_cursor() as (_, cur):
        cur.execute(
            """
            UPDATE outbox_events
            SET attempt_count = attempt_count + 1,
                last_error = %s,
                next_attempt_at = CASE
                    WHEN attempt_count + 1 >= %s THEN NOW()
                    ELSE NOW() + (%s * INTERVAL '1 second')
                END,
                dead_lettered_at = CASE
                    WHEN attempt_count + 1 >= %s THEN NOW()
                    ELSE NULL
                END,
                locked_by = NULL,
                locked_until = NULL
            WHERE event_id = %s
              AND locked_by = %s
              AND locked_until >= NOW()
              AND published_at IS NULL
              AND dead_lettered_at IS NULL
            RETURNING event_id
            """,
            (safe_error, max_attempts, retry_seconds, max_attempts, event_id, worker_id),
        )
        return cur.fetchone() is not None


def list_events(status: str, limit: int) -> list[dict]:
    conditions = {
        "pending": "published_at IS NULL AND dead_lettered_at IS NULL AND last_error IS NULL",
        "failed": "published_at IS NULL AND dead_lettered_at IS NULL AND last_error IS NOT NULL",
        # Retained for the existing service-level caller; the admin API accepts dead_letter only.
        "dead_lettered": "published_at IS NULL AND dead_lettered_at IS NOT NULL",
        "dead_letter": "published_at IS NULL AND dead_lettered_at IS NOT NULL",
    }
    condition = conditions.get(status)
    if condition is None:
        raise ValueError("未知 Outbox 事件状态")
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")

    with get_cursor() as (_, cur):
        cur.execute(
            f"""
            SELECT * FROM outbox_events
            WHERE {condition}
            ORDER BY occurred_at ASC, event_id ASC
            LIMIT %s
            """,
            (limit,),
        )
        return _rows_to_dicts(cur)


def get_event(event_id: str) -> dict | None:
    with get_cursor() as (_, cur):
        cur.execute("SELECT * FROM outbox_events WHERE event_id = %s", (event_id,))
        return _row_to_dict(cur, cur.fetchone())


def get_event_with_cursor(cur: PgCursor, event_id: str) -> dict | None:
    """Read an event in the caller-owned transaction for replay eligibility checks."""
    cur.execute("SELECT * FROM outbox_events WHERE event_id = %s", (event_id,))
    return _row_to_dict(cur, cur.fetchone())


def get_replay_state_with_cursor(cur: PgCursor, event_id: str) -> dict | None:
    """Read replay eligibility using PostgreSQL time in the caller-owned transaction."""
    cur.execute(
        """
        SELECT *, (locked_by IS NOT NULL AND locked_until > NOW()) AS lease_active
        FROM outbox_events
        WHERE event_id = %s
        """,
        (event_id,),
    )
    return _row_to_dict(cur, cur.fetchone())


def replay_event_with_cursor(cur: PgCursor, event_id: str) -> dict | None:
    """Queue an unpublished failed/dead-letter event without erasing delivery history."""
    cur.execute(
        """
        UPDATE outbox_events
        SET last_error = NULL,
            next_attempt_at = NOW(),
            locked_by = NULL,
            locked_until = NULL,
            dead_lettered_at = NULL
        WHERE event_id = %s
          AND published_at IS NULL
          AND NOT (locked_by IS NOT NULL AND locked_until > NOW())
          AND (
              dead_lettered_at IS NOT NULL
              OR (attempt_count > 0 AND last_error IS NOT NULL)
          )
        RETURNING *
        """,
        (event_id,),
    )
    return _row_to_dict(cur, cur.fetchone())


def _rows_to_dicts(cur: PgCursor) -> list[dict]:
    columns = [description[0] for description in cur.description]
    return [_convert_row(dict(zip(columns, row))) for row in cur.fetchall()]


def _row_to_dict(cur: PgCursor, row: tuple[Any, ...] | None) -> dict | None:
    if row is None:
        return None
    columns = [description[0] for description in cur.description]
    return _convert_row(dict(zip(columns, row)))


def _convert_row(row: dict) -> dict:
    for field_name in ("event_id", "aggregate_id"):
        if field_name in row and row[field_name] is not None:
            row[field_name] = str(row[field_name])
    if row.get("last_error") is not None:
        row["last_error"] = sanitize_delivery_error(row["last_error"])
    return row
