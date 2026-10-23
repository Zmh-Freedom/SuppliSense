"""Durable PostgreSQL queue for independently executed Agent Harness jobs."""

from typing import Any
from uuid import uuid4

from app.db.postgres import PgCursor, get_cursor
from app.domains.agent_run.coordinator import (
    PUBLIC_TERMINAL,
    claim_with_cursor,
    lock_execution,
    lock_run,
    takeover_with_cursor,
)
from app.domains.agent_run.repo import (
    _row_to_dict,
    append_event_with_cursor,
    get_run_for_update,
    update_run_status,
)


def enqueue_job_with_cursor(
    cur: PgCursor,
    run_id: str,
    *,
    queue: str = "agent",
    max_recoveries: int = 3,
) -> dict[str, Any]:
    """Create one durable job per Run; retries keep the existing row."""
    cur.execute(
        """INSERT INTO agent_execution_jobs (id, run_id, queue, max_recoveries)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (run_id) DO NOTHING
           RETURNING *""",
        (str(uuid4()), run_id, queue, max_recoveries),
    )
    row = _row_to_dict(cur, cur.fetchone())
    if row is not None:
        return row
    cur.execute("SELECT * FROM agent_execution_jobs WHERE run_id=%s", (run_id,))
    existing = _row_to_dict(cur, cur.fetchone())
    if existing is None:
        raise RuntimeError("Agent 执行任务创建失败")
    return existing


def enqueue_job(run_id: str, *, queue: str = "agent", max_recoveries: int = 3) -> dict[str, Any]:
    with get_cursor() as (_, cur):
        return enqueue_job_with_cursor(
            cur, run_id, queue=queue, max_recoveries=max_recoveries
        )


def get_job_for_run(run_id: str) -> dict[str, Any] | None:
    with get_cursor() as (_, cur):
        cur.execute("SELECT * FROM agent_execution_jobs WHERE run_id=%s", (run_id,))
        return _row_to_dict(cur, cur.fetchone())


def finish_job_with_cursor(
    cur: PgCursor,
    run_id: str,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Keep the queue row consistent with the authoritative execution state."""
    if status not in {"PAUSED", "SUCCEEDED", "FAILED", "CANCELLED"}:
        raise ValueError("无效的执行任务终态")
    cur.execute(
        """UPDATE agent_execution_jobs
           SET status=%s, last_error=%s, lease_owner=NULL, lease_until=NULL,
               completed_at=CASE WHEN %s IN ('SUCCEEDED','FAILED','CANCELLED')
                                 THEN clock_timestamp() ELSE NULL END,
               updated_at=clock_timestamp()
           WHERE run_id=%s AND status IN ('QUEUED','LEASED','PAUSED')""",
        (status, (error or None), status, run_id),
    )


def requeue_job_with_cursor(cur: PgCursor, run_id: str) -> None:
    """Requeue an explicitly resumed Run without changing its recovery history."""
    cur.execute(
        """UPDATE agent_execution_jobs
           SET status='QUEUED', available_at=clock_timestamp(), lease_owner=NULL,
               lease_until=NULL, attempt_id=NULL, fencing_token=NULL,
               updated_at=clock_timestamp()
           WHERE run_id=%s AND status='PAUSED'""",
        (run_id,),
    )


def heartbeat_job(job_id: str, worker_id: str, *, lease_seconds: int = 30) -> bool:
    with get_cursor() as (_, cur):
        cur.execute(
            """UPDATE agent_execution_jobs
               SET lease_until=clock_timestamp() + %s * INTERVAL '1 second',
                   updated_at=clock_timestamp()
               WHERE id=%s AND status='LEASED' AND lease_owner=%s
                 AND lease_until > clock_timestamp()""",
            (lease_seconds, job_id, worker_id),
        )
        return cur.rowcount == 1


def _pause_recovery_limit(
    cur: PgCursor,
    run: dict[str, Any],
    execution: dict[str, Any],
) -> None:
    cur.execute(
        """UPDATE agent_run_executions
           SET state='PAUSED', pause_reason='recovery_limit_exceeded',
               fencing_token=fencing_token+1, owner_id=NULL, lease_until=NULL,
               updated_at=clock_timestamp()
           WHERE run_id=%s""",
        (run["id"],),
    )
    append_event_with_cursor(
        cur,
        str(run["id"]),
        int(run["version"]),
        "workflow_status",
        {
            "run_id": str(run["id"]),
            "status": "stopped",
            "stage": "decision",
            "message": "执行恢复次数超过上限，已保留结果，需要人工核对",
        },
    )
    finish_job_with_cursor(cur, str(run["id"]), "FAILED", error="recovery_limit_exceeded")


def claim_job(
    worker_id: str,
    *,
    lease_seconds: int = 30,
    queue: str = "agent",
    max_recoveries: int = 3,
) -> dict[str, Any] | None:
    """Lease one due job and a matching attempt in the same short transaction.

    A Run row lock serializes claims for the same execution, so two workers can
    never both mutate its attempt.  The job row itself is updated conditionally
    after that lock, which avoids taking Job -> Run while cancel takes Run -> Job.
    """
    if lease_seconds < 3:
        raise ValueError("执行租约必须至少为 3 秒")
    with get_cursor() as (_, cur):
        cur.execute(
            """SELECT id, run_id FROM agent_execution_jobs
               WHERE queue=%s AND (
                   (status='QUEUED' AND available_at <= clock_timestamp())
                   OR (status='LEASED' AND lease_until <= clock_timestamp())
               )
               ORDER BY available_at, created_at
               LIMIT 1""",
            (queue,),
        )
        candidate = cur.fetchone()
        if candidate is None:
            return None
        job_id, run_id = str(candidate[0]), str(candidate[1])

        run = lock_run(cur, run_id)
        if run["status"] in PUBLIC_TERMINAL:
            finish_job_with_cursor(
                cur, run_id, "SUCCEEDED" if run["status"] == "COMPLETED" else "FAILED",
                error=None if run["status"] == "COMPLETED" else run["status"],
            )
            return None

        execution = lock_execution(cur, run_id)
        if execution is None:
            cur.execute(
                "INSERT INTO agent_run_executions (run_id,state) VALUES (%s,'QUEUED') ON CONFLICT (run_id) DO NOTHING",
                (run_id,),
            )
            execution = lock_execution(cur, run_id)
        if execution is None:
            raise RuntimeError("Agent 执行记录创建失败")

        # Someone else already holds a valid attempt. Do not steal it even if
        # a stale job lease happens to look expired; its heartbeat will keep
        # the queue row aligned with the execution lease.
        if execution["state"] == "RUNNING" and execution["lease_valid"]:
            return None
        if execution["state"] not in {"QUEUED", "RUNNING"}:
            if execution["state"] == "CANCEL_REQUESTED":
                finish_job_with_cursor(cur, run_id, "CANCELLED", error=None)
            return None

        cur.execute(
            """UPDATE agent_execution_jobs
               SET status='LEASED', lease_owner=%s,
                   lease_until=clock_timestamp() + %s * INTERVAL '1 second',
                   attempts=attempts+1, updated_at=clock_timestamp()
               WHERE id=%s AND (
                   (status='QUEUED' AND available_at <= clock_timestamp())
                   OR (status='LEASED' AND lease_until <= clock_timestamp())
               )
               RETURNING *""",
            (worker_id, lease_seconds, job_id),
        )
        job = _row_to_dict(cur, cur.fetchone())
        if job is None:
            return None

        recovery = execution["state"] == "RUNNING"
        if recovery:
            if int(job["recoveries"]) >= int(job["max_recoveries"]):
                _pause_recovery_limit(cur, run, execution)
                return None
            permit = takeover_with_cursor(
                cur, run, execution, owner_id=worker_id, lease_seconds=lease_seconds
            )
            cur.execute(
                """UPDATE agent_execution_jobs
                   SET recoveries=recoveries+1, attempt_id=%s, fencing_token=%s,
                       updated_at=clock_timestamp()
                   WHERE id=%s RETURNING *""",
                (permit.attempt_id, permit.fencing_token, job_id),
            )
        else:
            permit = claim_with_cursor(
                cur, run, owner_id=worker_id, lease_seconds=lease_seconds
            )
            cur.execute(
                """UPDATE agent_execution_jobs
                   SET attempt_id=%s, fencing_token=%s, updated_at=clock_timestamp()
                   WHERE id=%s RETURNING *""",
                (permit.attempt_id, permit.fencing_token, job_id),
            )
        job = _row_to_dict(cur, cur.fetchone())
        if job is None:
            raise RuntimeError("Agent 执行任务领取失败")
        job["job_id"] = str(job.pop("id"))
        job["permit"] = permit
        job["recovery"] = recovery
        return job


def claim_candidate_review_batch(
    worker_id: str,
    *,
    lease_seconds: int = 30,
    max_attempts: int = 3,
) -> dict[str, Any] | None:
    """Lease one durable sourcing review batch without entering Harness execution."""
    if lease_seconds < 3:
        raise ValueError("执行租约必须至少为 3 秒")
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT batch_id, run_id FROM sourcing_candidate_review_events
            WHERE dispatch_status='pending'
               OR (dispatch_status='processing' AND lease_until <= clock_timestamp())
            ORDER BY event_id
            LIMIT 1
            """
        )
        candidate = cur.fetchone()
        if candidate is None:
            return None
        batch_id, run_id = str(candidate[0]), str(candidate[1])

        run = get_run_for_update(run_id, cur)
        if run is None:
            return None
        cur.execute(
            """SELECT *, lease_until IS NOT NULL AND lease_until > clock_timestamp() AS lease_active
               FROM sourcing_candidate_review_events WHERE batch_id=%s FOR UPDATE""",
            (batch_id,),
        )
        event = _row_to_dict(cur, cur.fetchone())
        if event is None or event["dispatch_status"] not in {"pending", "processing"}:
            return None
        if event["dispatch_status"] == "processing" and event.get("lease_active"):
            return None

        if run["status"] in {"CANCELLED", "ROLLBACK_FROZEN"}:
            cur.execute(
                """UPDATE sourcing_candidate_review_events
                   SET dispatch_status='cancelled', lease_owner=NULL, lease_until=NULL
                   WHERE batch_id=%s""",
                (batch_id,),
            )
            return {"batch_id": batch_id, "run_id": run_id, "status": "cancelled"}

        if run["status"] != "IDENTITY_RESOLVING" or int(run["version"]) != int(event["run_version"]):
            cur.execute(
                """UPDATE sourcing_candidate_review_events
                   SET dispatch_status='failed', lease_owner=NULL, lease_until=NULL,
                       last_error='stale_run_version'
                   WHERE batch_id=%s""",
                (batch_id,),
            )
            return {"batch_id": batch_id, "run_id": run_id, "status": "stale"}

        if int(event["attempt_count"]) >= max_attempts:
            cur.execute(
                """UPDATE sourcing_candidate_review_events
                   SET dispatch_status='failed', lease_owner=NULL, lease_until=NULL,
                       last_error='recovery_limit_exceeded'
                   WHERE batch_id=%s""",
                (batch_id,),
            )
            updated = update_run_status(
                run_id, int(run["version"]), "NEEDS_REVIEW", "CANDIDATE_REVIEW_RECOVERY_LIMIT", cur=cur
            )
            if updated is not None:
                append_event_with_cursor(
                    cur,
                    run_id,
                    int(updated["version"]),
                    "candidate_review_failed",
                    {"batch_id": batch_id, "status": "NEEDS_REVIEW", "reason": "recovery_limit_exceeded"},
                )
            return {"batch_id": batch_id, "run_id": run_id, "status": "failed"}

        cur.execute(
            """
            UPDATE sourcing_candidate_review_events
            SET dispatch_status='processing', lease_owner=%s,
                lease_until=clock_timestamp() + %s * INTERVAL '1 second',
                attempt_count=attempt_count+1, fencing_token=fencing_token+1,
                last_error=NULL
            WHERE batch_id=%s
            RETURNING batch_id, run_id, run_version, fencing_token, attempt_count
            """,
            (worker_id, lease_seconds, batch_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "batch_id": str(row[0]),
            "run_id": str(row[1]),
            "run_version": int(row[2]),
            "fencing_token": int(row[3]),
            "attempt_count": int(row[4]),
            "status": "processing",
        }


def heartbeat_candidate_review_batch(
    batch_id: str,
    worker_id: str,
    fencing_token: int,
    *,
    lease_seconds: int = 30,
) -> bool:
    with get_cursor() as (_, cur):
        cur.execute(
            """UPDATE sourcing_candidate_review_events
               SET lease_until=clock_timestamp() + %s * INTERVAL '1 second'
               WHERE batch_id=%s AND dispatch_status='processing'
                 AND lease_owner=%s AND fencing_token=%s
                 AND lease_until > clock_timestamp()
               RETURNING batch_id""",
            (lease_seconds, batch_id, worker_id, fencing_token),
        )
        return cur.fetchone() is not None


__all__ = [
    "claim_job",
    "claim_candidate_review_batch",
    "enqueue_job",
    "enqueue_job_with_cursor",
    "finish_job_with_cursor",
    "get_job_for_run",
    "heartbeat_job",
    "heartbeat_candidate_review_batch",
    "requeue_job_with_cursor",
]
