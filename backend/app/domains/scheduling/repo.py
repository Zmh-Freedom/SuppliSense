"""Persistence for immutable schedule revisions and deduplicated period jobs."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from psycopg2.extras import Json

from app.core.config import settings
from app.db.postgres import PgCursor, get_cursor
from app.domains.scheduling.errors import ExecutionDenied
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
        from app.domains.scheduling.executor import EXECUTABLE_KINDS

        runnable = definition.kind in EXECUTABLE_KINDS and (
            definition.kind not in POLICY_GATED_KINDS
            or settings.SCHEDULER_PAID_EXECUTION_ENABLED
        )
        status = "QUEUED" if runnable else "BLOCKED_POLICY"
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


__all__ = [
    "assert_scheduled_lease", "claim_scheduled_job", "enqueue_due_schedule", "finish_scheduled_job",
    "heartbeat_scheduled_job", "list_due_schedules", "mark_schedule_checked",
    "register_schedule",
]


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


def _row_dict(cur: PgCursor, row) -> dict[str, Any] | None:
    if row is None:
        return None
    return {column[0]: value for column, value in zip(cur.description, row, strict=True)}


def claim_scheduled_job(worker_id: str, *, lease_seconds: int = 30) -> dict[str, Any] | None:
    """Lease one due job; expired leases are recoverable by another owner."""
    if lease_seconds < 3:
        raise ValueError("执行租约必须至少为 3 秒")
    with get_cursor() as (_, cur):
        cur.execute(
            """SELECT id FROM scheduled_execution_jobs
               WHERE status='QUEUED'
                  OR (status='LEASED' AND lease_until <= clock_timestamp())
               ORDER BY fire_at, created_at
               LIMIT 1
               FOR UPDATE SKIP LOCKED"""
        )
        candidate = cur.fetchone()
        if candidate is None:
            return None
        job_id = str(candidate[0])
        cur.execute(
            "SELECT * FROM scheduled_execution_jobs WHERE id=%s FOR UPDATE", (job_id,)
        )
        row = _row_dict(cur, cur.fetchone())
        if row is None:
            return None
        if (row["status"] == "LEASED" and row["kind"] in POLICY_GATED_KINDS
                and row.get("provider_started_at") is not None):
            cur.execute(
                """UPDATE scheduled_execution_jobs
                   SET status='NEEDS_REVIEW', last_error='external_result_unknown',
                       lease_owner=NULL, lease_until=NULL, completed_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE id=%s""",
                (job_id,),
            )
            return None
        if int(row["attempts"]) >= int(row["max_attempts"]):
            cur.execute(
                """UPDATE scheduled_execution_jobs
                   SET status='NEEDS_REVIEW', last_error='attempt_limit_exceeded',
                       lease_owner=NULL, lease_until=NULL, completed_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE id=%s""",
                (job_id,),
            )
            return None
        cur.execute(
            """UPDATE scheduled_execution_jobs
               SET status='LEASED', lease_owner=%s,
                   lease_until=clock_timestamp() + %s * INTERVAL '1 second',
                   attempts=attempts+1, fencing_token=fencing_token+1,
                   started_at=COALESCE(started_at, clock_timestamp()),
                   last_error=NULL, updated_at=clock_timestamp()
               WHERE id=%s
               RETURNING *""",
            (worker_id, lease_seconds, job_id),
        )
        job = _row_dict(cur, cur.fetchone())
        if job is None:
            return None
    job["id"] = str(job["id"])
    return job


def heartbeat_scheduled_job(
    job_id: str, worker_id: str, fencing_token: int, *, lease_seconds: int = 30
) -> bool:
    with get_cursor() as (_, cur):
        cur.execute(
            """UPDATE scheduled_execution_jobs
               SET lease_until=clock_timestamp() + %s * INTERVAL '1 second',
                   updated_at=clock_timestamp()
               WHERE id=%s AND status='LEASED' AND lease_owner=%s
                 AND fencing_token=%s AND lease_until > clock_timestamp()""",
            (lease_seconds, job_id, worker_id, fencing_token),
        )
        return cur.rowcount == 1


def finish_scheduled_job(
    job: dict[str, Any],
    worker_id: str,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> bool:
    """Commit a terminal state and its audit record under the current fence."""
    if status not in {"SUCCEEDED", "FAILED", "NEEDS_REVIEW", "CANCELLED", "BLOCKED_POLICY"}:
        raise ValueError("无效的周期任务终态")
    from app.domains.auth.audit_repo import create_log_with_cursor

    details = {
        "job_id": job["id"],
        "schedule_id": job["schedule_id"],
        "schedule_revision": job["schedule_revision"],
        "target_id": job["target_id"],
        "logical_period": job["logical_period"],
        "kind": job["kind"],
        "actor_kind": job["actor_kind"],
        "actor_id": job["actor_id"],
        "status": status,
    }
    if error:
        details["error"] = error[:500]
    user_id = None
    if job["actor_kind"] == "user":
        from uuid import UUID

        try:
            user_id = str(UUID(str(job["actor_id"])))
        except (TypeError, ValueError):
            user_id = None
    with get_cursor() as (_, cur):
        if user_id is not None:
            # Audit must survive user deletion; an absent FK target is kept
            # in details instead of aborting the terminal transition.
            cur.execute("SELECT 1 FROM users WHERE id=%s", (user_id,))
            if cur.fetchone() is None:
                user_id = None
        cur.execute(
            """UPDATE scheduled_execution_jobs
               SET status=%s, result=%s, last_error=%s, lease_owner=NULL,
                   lease_until=NULL, completed_at=clock_timestamp(), updated_at=clock_timestamp()
               WHERE id=%s AND status='LEASED' AND lease_owner=%s AND fencing_token=%s""",
            (
                status, Json(result or {}), error[:4000] if error else None,
                job["id"], worker_id, job["fencing_token"],
            ),
        )
        if cur.rowcount != 1:
            return False
        create_log_with_cursor(
            cur,
            "scheduled.execution.finished",
            user_id=user_id,
            resource_type="scheduled_execution_job",
            resource_id=job["id"],
            details=details,
        )
    return True


def mark_provider_started(job: dict[str, Any], worker_id: str) -> None:
    """Persist the external-call boundary before the provider is invoked."""
    with get_cursor() as (_, cur):
        cur.execute(
            """UPDATE scheduled_execution_jobs
               SET provider_started_at=COALESCE(provider_started_at, clock_timestamp()),
                   updated_at=clock_timestamp()
               WHERE id=%s AND status='LEASED' AND lease_owner=%s
                 AND fencing_token=%s AND lease_until > clock_timestamp()""",
            (job["id"], worker_id, job["fencing_token"]),
        )
        if cur.rowcount != 1:
            raise ExecutionDenied("lease_lost")


def assert_scheduled_lease(job: dict[str, Any], worker_id: str) -> None:
    """Fail closed if another owner has taken over before an external call."""
    with get_cursor() as (_, cur):
        cur.execute(
            """SELECT 1 FROM scheduled_execution_jobs
               WHERE id=%s AND status='LEASED' AND lease_owner=%s
                 AND fencing_token=%s AND lease_until > clock_timestamp()""",
            (job["id"], worker_id, job["fencing_token"]),
        )
        if cur.fetchone() is None:
            raise ExecutionDenied("lease_lost")


def list_schedules(*, schedule_id: str | None = None) -> list[dict[str, Any]]:
    """Inspect persisted definitions without executing them."""
    with get_cursor() as (_, cur):
        if schedule_id:
            cur.execute(
                """SELECT schedule_id,revision,target_id,cron,timezone,actor_kind,
                          actor_id,kind,scope,effective_at,enabled,last_checked_at
                   FROM durable_schedules WHERE schedule_id=%s
                   ORDER BY revision DESC, target_id""",
                (schedule_id,),
            )
        else:
            cur.execute(
                """SELECT schedule_id,revision,target_id,cron,timezone,actor_kind,
                          actor_id,kind,scope,effective_at,enabled,last_checked_at
                   FROM durable_schedules ORDER BY schedule_id,revision DESC, target_id"""
            )
        rows = cur.fetchall()
    keys = (
        "schedule_id", "revision", "target_id", "cron", "timezone", "actor_kind",
        "actor_id", "kind", "scope", "effective_at", "enabled", "last_checked_at",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]
