"""Persistence for immutable schedule revisions and deduplicated period jobs."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from psycopg2.extras import Json

from app.db.postgres import PgCursor, get_cursor
from app.domains.scheduling.periods import (
    POLICY_GATED_KINDS,
    ScheduleDefinition,
    latest_due_period,
)

_DEFINITION_COLUMNS = (
    "schedule_id", "revision", "target_id", "cron", "timezone", "actor_kind",
    "actor_id", "kind", "scope", "effective_at",
)


def _definition_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    payload = dict(zip(_DEFINITION_COLUMNS, row, strict=True))
    payload["effective_at"] = payload["effective_at"].isoformat()
    return payload


def register_schedule(definition: ScheduleDefinition) -> dict[str, Any]:
    """Insert a revision once; an identical retry is idempotent.

    Reusing a revision with different contents is a programming error because
    jobs already reference the original definition.
    """
    payload = definition.to_payload()
    with get_cursor() as (_, cur):
        cur.execute(
            """INSERT INTO durable_schedules
               (schedule_id,revision,target_id,cron,timezone,actor_kind,actor_id,
                kind,scope,effective_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (schedule_id,revision,target_id) DO NOTHING
               RETURNING schedule_id""",
            (
                definition.schedule_id, definition.revision, definition.target_id,
                definition.cron, definition.timezone, definition.actor_kind,
                definition.actor_id, definition.kind, Json(definition.scope),
                definition.effective_at,
            ),
        )
        inserted = cur.fetchone() is not None
        if not inserted:
            cur.execute(
                """SELECT schedule_id,revision,target_id,cron,timezone,actor_kind,
                          actor_id,kind,scope,effective_at
                   FROM durable_schedules
                   WHERE schedule_id=%s AND revision=%s AND target_id=%s""",
                (definition.schedule_id, definition.revision, definition.target_id),
            )
            existing = cur.fetchone()
            if existing is None or _definition_from_row(existing) != payload:
                raise ValueError("同一计划版本的定义不可修改，请递增 revision")
    return {**payload, "created": inserted}


def list_due_schedules(batch_size: int = 20) -> list[tuple[str, str]]:
    """Return enabled schedules least recently checked first."""
    if batch_size < 1:
        raise ValueError("batch_size 必须为正整数")
    with get_cursor() as (_, cur):
        cur.execute(
            """SELECT schedule_id,target_id FROM durable_schedules
               WHERE enabled
               ORDER BY last_checked_at NULLS FIRST, schedule_id, target_id
               LIMIT %s""",
            (batch_size,),
        )
        return [(str(row[0]), str(row[1])) for row in cur.fetchall()]


def _latest_definition(
    cur: PgCursor, schedule_id: str, target_id: str
) -> dict[str, Any] | None:
    cur.execute(
        """SELECT schedule_id,revision,target_id,cron,timezone,actor_kind,actor_id,
                  kind,scope,effective_at
           FROM durable_schedules
           WHERE schedule_id=%s AND target_id=%s AND enabled
           ORDER BY revision DESC LIMIT 1""",
        (schedule_id, target_id),
    )
    row = cur.fetchone()
    return _definition_from_row(row) if row else None


def enqueue_due_schedule(
    schedule_id: str, target_id: str, *, now: datetime | None = None
) -> dict[str, Any] | None:
    """Create at most one job for the latest due period of a schedule.

    Policy-gated kinds are recorded as BLOCKED_POLICY until permission, budget and
    audit gates are implemented. Recording them keeps the period auditable and
    deduplicated without performing an external call.
    """
    with get_cursor() as (_, cur):
        if now is None:
            # PostgreSQL clock is the only shared time source across schedulers.
            cur.execute("SELECT clock_timestamp()")
            now = cur.fetchone()[0]
        payload = _latest_definition(cur, schedule_id, target_id)
        if payload is None:
            return None
        definition = ScheduleDefinition.from_payload(payload)
        period = latest_due_period(definition, now)
        cur.execute(
            """UPDATE durable_schedules SET last_checked_at=%s
               WHERE schedule_id=%s AND revision=%s AND target_id=%s""",
            (now, schedule_id, definition.revision, target_id),
        )
        if period is None:
            return None
        status = "BLOCKED_POLICY" if definition.kind in POLICY_GATED_KINDS else "QUEUED"
        job_id = str(uuid4())
        cur.execute(
            """INSERT INTO scheduled_execution_jobs
               (id,schedule_id,schedule_revision,target_id,logical_period,
                period_start_at,period_end_at,fire_at,kind,actor_kind,actor_id,
                scope,definition,status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (schedule_id,schedule_revision,target_id,logical_period)
               DO NOTHING
               RETURNING id,status,created_at""",
            (
                job_id, schedule_id, definition.revision, target_id,
                period.logical_period, period.start_at, period.end_at, period.fire_at,
                definition.kind, definition.actor_kind, definition.actor_id,
                Json(definition.scope), Json(definition.to_payload()), status,
            ),
        )
        row = cur.fetchone()
        created = row is not None
        if not created:
            cur.execute(
                """SELECT id,status,created_at FROM scheduled_execution_jobs
                   WHERE schedule_id=%s AND schedule_revision=%s
                     AND target_id=%s AND logical_period=%s""",
                (schedule_id, definition.revision, target_id, period.logical_period),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("周期任务创建失败")
    return {
        "job_id": str(row[0]),
        "status": row[1],
        "created": created,
        "schedule_id": schedule_id,
        "schedule_revision": definition.revision,
        "target_id": target_id,
        "logical_period": period.logical_period,
        "period_start_at": period.start_at,
        "period_end_at": period.end_at,
        "fire_at": period.fire_at,
        "kind": definition.kind,
    }


__all__ = ["enqueue_due_schedule", "list_due_schedules", "register_schedule"]


def mark_schedule_checked(
    schedule_id: str, target_id: str, *, now: datetime | None = None
) -> None:
    """Advance the fairness cursor after a failed scan.

    Without this, a permanently malformed plan would always sort first and
    starve every valid schedule behind it.
    """
    with get_cursor() as (_, cur):
        cur.execute(
            """UPDATE durable_schedules SET last_checked_at=%s
               WHERE schedule_id=%s AND target_id=%s AND enabled""",
            (now or datetime.now(UTC), schedule_id, target_id),
        )
