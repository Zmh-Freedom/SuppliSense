"""Transactional ownership of execution, independent of graphs and transport."""

from dataclasses import dataclass
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Any
from uuid import uuid4

from psycopg2.extras import Json

from app.db.postgres import PgCursor, get_cursor
from app.domains.agent_run.repo import _row_to_dict, append_event_with_cursor


class ExecutionState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


PUBLIC_TERMINAL = frozenset({
    "COMPLETED", "PARTIAL", "NEEDS_REVIEW", "ACTION_FAILED", "FAILED",
    "CANCELLED", "ROLLBACK_FROZEN",
})
_TRANSITIONS = {
    ExecutionState.QUEUED: {ExecutionState.RUNNING, ExecutionState.CANCELLED, ExecutionState.FAILED},
    ExecutionState.RUNNING: {
        ExecutionState.PAUSED, ExecutionState.CANCEL_REQUESTED,
        ExecutionState.SUCCEEDED, ExecutionState.FAILED,
    },
    ExecutionState.PAUSED: {ExecutionState.QUEUED, ExecutionState.CANCELLED, ExecutionState.FAILED},
    ExecutionState.CANCEL_REQUESTED: {ExecutionState.CANCELLED},
}


class ExecutionLost(RuntimeError):
    """The attempt no longer has permission to publish or start work."""


@dataclass(frozen=True)
class ExecutionPermit:
    run_id: str
    attempt_id: str
    fencing_token: int


current_execution: ContextVar[ExecutionPermit | None] = ContextVar("agent_execution", default=None)


@contextmanager
def execution_scope(permit: ExecutionPermit) -> Iterator[None]:
    token = current_execution.set(permit)
    try:
        yield
    finally:
        current_execution.reset(token)


def check_current_execution() -> None:
    permit = current_execution.get()
    if permit is not None:
        run_coordinator.check(permit)


def can_transition(current: ExecutionState, target: ExecutionState) -> bool:
    return target in _TRANSITIONS.get(current, set())


def public_status(state: ExecutionState, quality: str = "complete") -> str:
    if state == ExecutionState.SUCCEEDED:
        return {"partial": "PARTIAL", "needs_review": "NEEDS_REVIEW"}.get(quality, "COMPLETED")
    return {ExecutionState.QUEUED: "RUNNING", ExecutionState.PAUSED: "RUNNING"}.get(state, state.value)


def lock_run(cur: PgCursor, run_id: str) -> dict[str, Any]:
    cur.execute("SELECT * FROM agent_runs WHERE id = %s FOR UPDATE", (run_id,))
    run = _row_to_dict(cur, cur.fetchone())
    if run is None:
        raise ValueError("Agent Run 不存在")
    return run


def lock_execution(cur: PgCursor, run_id: str) -> dict[str, Any] | None:
    cur.execute(
        "SELECT *, lease_until > clock_timestamp() AS lease_valid FROM agent_run_executions WHERE run_id = %s FOR UPDATE",
        (run_id,),
    )
    return _row_to_dict(cur, cur.fetchone())


def ensure_execution(cur: PgCursor, run_id: str) -> dict[str, Any]:
    """Create the execution row before a short ownership transaction claims it."""
    cur.execute(
        "INSERT INTO agent_run_executions (run_id, state) VALUES (%s,'QUEUED') ON CONFLICT (run_id) DO NOTHING",
        (run_id,),
    )
    execution = lock_execution(cur, run_id)
    if execution is None:
        raise RuntimeError("Agent 执行记录创建失败")
    return execution


def claim_with_cursor(
    cur: PgCursor,
    run: dict[str, Any],
    *,
    owner_id: str,
    lease_seconds: int,
) -> ExecutionPermit:
    """Claim a queued execution; caller holds the Run and Execution row locks."""
    if run["status"] in PUBLIC_TERMINAL:
        raise ExecutionLost("Agent Run 已结束")
    execution = ensure_execution(cur, str(run["id"]))
    if execution["state"] != ExecutionState.QUEUED:
        raise ExecutionLost("Agent Run 已有执行者或需要恢复核验")
    attempt_id = str(uuid4())
    token = int(execution["fencing_token"]) + 1
    cur.execute(
        """UPDATE agent_run_executions SET state='RUNNING', attempt_id=%s,
           fencing_token=%s, owner_id=%s,
           lease_until=clock_timestamp() + %s * INTERVAL '1 second', updated_at=clock_timestamp()
           WHERE run_id=%s""",
        (attempt_id, token, owner_id, lease_seconds, run["id"]),
    )
    return ExecutionPermit(str(run["id"]), attempt_id, token)


def takeover_with_cursor(
    cur: PgCursor,
    run: dict[str, Any],
    execution: dict[str, Any],
    *,
    owner_id: str,
    lease_seconds: int,
) -> ExecutionPermit:
    """Replace an expired attempt with a new fencing token.

    The caller holds Run and Execution locks.  The old worker may still be
    running, but every subsequent check sees the new token and is rejected.
    """
    if run["status"] in PUBLIC_TERMINAL:
        raise ExecutionLost("Agent Run 已结束")
    if execution["state"] != ExecutionState.RUNNING or execution["lease_valid"]:
        raise ExecutionLost("Agent 执行许可仍有效，不能接管")
    attempt_id = str(uuid4())
    token = int(execution["fencing_token"]) + 1
    cur.execute(
        """UPDATE agent_run_executions SET state='RUNNING', attempt_id=%s,
           fencing_token=%s, owner_id=%s, pause_reason=NULL,
           lease_until=clock_timestamp() + %s * INTERVAL '1 second',
           recovery_count=recovery_count+1, updated_at=clock_timestamp()
           WHERE run_id=%s""",
        (attempt_id, token, owner_id, lease_seconds, run["id"]),
    )
    return ExecutionPermit(str(run["id"]), attempt_id, token)


def require_active(cur: PgCursor, run: dict[str, Any], permit: ExecutionPermit | None) -> None:
    """Caller holds the Run lock; this same lock serializes tool authorization."""
    if run["status"] in PUBLIC_TERMINAL:
        raise ExecutionLost("Agent Run 已结束")
    execution = lock_execution(cur, str(run["id"]))
    if execution is None:
        if permit is not None:
            raise ExecutionLost("执行许可不存在")
        return  # Historical directed runners do not acquire execution ownership.
    if (
        permit is None
        or execution["state"] != ExecutionState.RUNNING
        or str(execution["attempt_id"]) != permit.attempt_id
        or int(execution["fencing_token"]) != permit.fencing_token
        or not execution["lease_valid"]
    ):
        raise ExecutionLost("Agent 执行许可已失效或已取消")


class RunCoordinator:
    def __init__(self, lease_seconds: int = 30) -> None:
        if lease_seconds < 3:
            raise ValueError("执行租约必须至少为 3 秒")
        self.lease_seconds = lease_seconds

    def claim(self, run_id: str, owner_id: str) -> ExecutionPermit:
        with get_cursor() as (_, cur):
            run = lock_run(cur, run_id)
            return claim_with_cursor(
                cur, run, owner_id=owner_id, lease_seconds=self.lease_seconds
            )

    def check(self, permit: ExecutionPermit) -> None:
        with get_cursor() as (_, cur):
            require_active(cur, lock_run(cur, permit.run_id), permit)

    def is_paused(self, run_id: str) -> bool:
        with get_cursor() as (_, cur):
            cur.execute("SELECT state FROM agent_run_executions WHERE run_id=%s", (run_id,))
            row = cur.fetchone()
            return row is not None and row[0] == ExecutionState.PAUSED

    def heartbeat(self, permit: ExecutionPermit) -> bool:
        try:
            with get_cursor() as (_, cur):
                require_active(cur, lock_run(cur, permit.run_id), permit)
                cur.execute(
                    "UPDATE agent_run_executions SET lease_until=clock_timestamp() + %s * INTERVAL '1 second', updated_at=clock_timestamp() WHERE run_id=%s",
                    (self.lease_seconds, permit.run_id),
                )
            return True
        except ExecutionLost:
            return False

    def recovery_record(self, run_id: str, user_id: str) -> dict[str, Any]:
        with get_cursor() as (_, cur):
            run = lock_run(cur, run_id)
            if str(run.get("user_id") or "") != user_id:
                raise PermissionError("Agent Run 不属于当前用户")
            execution = lock_execution(cur, run_id)
            if not execution or not execution.get("execution_input"):
                raise ExecutionLost("历史任务缺少可核对的冻结输入，请取消后重新发起")
            if execution["execution_input"].get("runtime") == "harness":
                cur.execute(
                    """SELECT event_id,event_type,payload FROM agent_run_events
                       WHERE run_id=%s AND event_type IN ('tool_call','tool_result')
                       ORDER BY event_id""",
                    (run_id,),
                )
                tool_events = [
                    {"event_id": int(event_id), "event_type": event_type, "payload": payload or {}}
                    for event_id, event_type, payload in cur.fetchall()
                ]
                execution["reserved_tool_calls"] = sum(
                    event["event_type"] == "tool_call" for event in tool_events
                )
                execution["committed_tool_events"] = tool_events
            return {"run": run, **execution}

    def save_snapshot(self, permit: ExecutionPermit, snapshot: dict[str, Any]) -> None:
        with get_cursor() as (_, cur):
            require_active(cur, lock_run(cur, permit.run_id), permit)
            cur.execute("UPDATE agent_run_executions SET snapshot=%s,updated_at=clock_timestamp() WHERE run_id=%s",
                        (Json(snapshot), permit.run_id))

    def consume_recovery_checkpoint(self, permit: ExecutionPermit, proof: dict[str, Any]) -> None:
        """Consume a verified pause proof under the current execution fence."""
        with get_cursor() as (_, cur):
            run = lock_run(cur, permit.run_id)
            require_active(cur, run, permit)
            execution = lock_execution(cur, permit.run_id)
            if execution is None or execution.get("checkpoint") != proof:
                raise ExecutionLost("恢复检查点已被其他执行者处理")
            cur.execute(
                "UPDATE agent_run_executions SET checkpoint='{}'::jsonb,updated_at=clock_timestamp() WHERE run_id=%s",
                (permit.run_id,),
            )

    def pause(
        self, permit: ExecutionPermit, *, snapshot: dict[str, Any],
        checkpoint: dict[str, Any], reason: str,
        events: list[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Release an attempt and atomically publish its resumable pause."""
        with get_cursor() as (_, cur):
            run = lock_run(cur, permit.run_id)
            require_active(cur, run, permit)
            execution = lock_execution(cur, permit.run_id)
            cur.execute(
                """UPDATE agent_run_executions SET state='PAUSED',snapshot=%s,checkpoint=%s,
                   pause_reason=%s,owner_id=NULL,lease_until=NULL,updated_at=clock_timestamp()
                   WHERE run_id=%s""",
                (Json(snapshot), Json(checkpoint), reason, permit.run_id),
            )
            from app.domains.agent_run.execution_jobs import finish_job_with_cursor

            finish_job_with_cursor(cur, permit.run_id, "PAUSED", error=reason)
            cur.execute(
                "UPDATE agent_runs SET status=%s,version=version+1,updated_at=NOW() WHERE id=%s RETURNING version",
                ("ACTION_PENDING" if reason == "waiting_approval" else "RUNNING", permit.run_id),
            )
            version = int(cur.fetchone()[0])
            cur.execute("UPDATE agent_turns SET status=%s WHERE run_id=%s",
                        ("waiting_approval" if reason == "waiting_approval" else "running", permit.run_id))
            frozen = execution.get("execution_input") or {}
            if frozen and run.get("session_id"):
                metadata = {**dict(checkpoint.get("config") or {}), "managed_run_id": permit.run_id}
                cur.execute(
                    """INSERT INTO agent_chat_interrupts
                       (session_id,user_id,config,mode,user_message,status,claim_token,claimed_at)
                       VALUES (%s,%s,%s,%s,%s,'pending',NULL,NULL)
                       ON CONFLICT (session_id) DO UPDATE SET user_id=EXCLUDED.user_id,
                       config=EXCLUDED.config,mode=EXCLUDED.mode,user_message=EXCLUDED.user_message,
                       status='pending',claim_token=NULL,claimed_at=NULL,updated_at=NOW()""",
                    (run["session_id"], run["user_id"], Json(metadata), frozen["runtime"], frozen["user_message"]),
                )
            return [append_event_with_cursor(cur, permit.run_id, version, name, payload) for name, payload in events]

    def resume(self, run_id: str, user_id: str, *, checkpoint: dict[str, Any]) -> ExecutionPermit:
        """Grant one new attempt only for a verified paused checkpoint."""
        with get_cursor() as (_, cur):
            run = lock_run(cur, run_id)
            if str(run.get("user_id") or "") != user_id:
                raise PermissionError("Agent Run 不属于当前用户")
            execution = lock_execution(cur, run_id)
            if (run["status"] in PUBLIC_TERMINAL or not execution
                    or execution["state"] != ExecutionState.PAUSED
                    or execution["checkpoint"] != checkpoint):
                raise ExecutionLost("恢复检查点已变化或任务已有执行者")
            cur.execute("SELECT is_active FROM users WHERE id=%s", (user_id,))
            actor = cur.fetchone()
            if not actor or not actor[0]:
                raise PermissionError("用户已停用，无法恢复任务")
            attempt_id, token = str(uuid4()), int(execution["fencing_token"]) + 1
            cur.execute(
                """UPDATE agent_run_executions SET state='RUNNING',attempt_id=%s,fencing_token=%s,
                   owner_id='chat-resume',lease_until=clock_timestamp()+%s*INTERVAL '1 second',
                   updated_at=clock_timestamp() WHERE run_id=%s""",
                (attempt_id, token, self.lease_seconds, run_id),
            )
            from app.domains.agent_run.execution_jobs import requeue_job_with_cursor

            requeue_job_with_cursor(cur, run_id)
        return ExecutionPermit(run_id, attempt_id, token)

    def resume_for_worker(self, run_id: str, user_id: str) -> None:
        """Release a paused Run back to the durable queue without executing it."""
        with get_cursor() as (_, cur):
            run = lock_run(cur, run_id)
            if str(run.get("user_id") or "") != user_id:
                raise PermissionError("Agent Run 不属于当前用户")
            execution = lock_execution(cur, run_id)
            if (run["status"] in PUBLIC_TERMINAL or not execution
                    or execution["state"] != ExecutionState.PAUSED
                    or not execution.get("checkpoint")):
                raise ExecutionLost("恢复检查点已变化或任务已有执行者")
            cur.execute("SELECT is_active FROM users WHERE id=%s", (user_id,))
            actor = cur.fetchone()
            if not actor or not actor[0]:
                raise PermissionError("用户已停用，无法恢复任务")
            attempt_id, token = str(uuid4()), int(execution["fencing_token"]) + 1
            cur.execute(
                """UPDATE agent_run_executions SET state='RUNNING',attempt_id=%s,fencing_token=%s,
                   owner_id='worker-queue',lease_until=clock_timestamp(),
                   updated_at=clock_timestamp() WHERE run_id=%s""",
                (attempt_id, token, run_id),
            )
            from app.domains.agent_run.execution_jobs import requeue_job_with_cursor

            requeue_job_with_cursor(cur, run_id)

    def cancel(self, run_id: str, user_id: str, *, expected_version: int | None = None) -> dict[str, Any]:
        with get_cursor() as (_, cur):
            cur.execute("SELECT session_id FROM agent_runs WHERE id=%s", (run_id,))
            row = cur.fetchone()
            if row and row[0]:
                cur.execute("SELECT id FROM agent_sessions WHERE id=%s FOR UPDATE", (row[0],))
            run = lock_run(cur, run_id)
            if str(run.get("user_id") or "") != user_id:
                raise PermissionError("Agent Run 不属于当前用户")
            if expected_version is not None and int(run["version"]) != expected_version:
                from app.core.errors import DomainError

                raise DomainError("AGENT_RUN_VERSION_CONFLICT", "任务版本已变更", 409)
            if run["status"] in PUBLIC_TERMINAL:
                return run
            cur.execute(
                "INSERT INTO agent_run_executions (run_id,state) VALUES (%s,'QUEUED') ON CONFLICT (run_id) DO NOTHING",
                (run_id,),
            )
            execution = lock_execution(cur, run_id)
            if execution and execution["state"] == ExecutionState.RUNNING:
                cur.execute(
                    "UPDATE agent_run_executions SET state='CANCEL_REQUESTED',cancel_requested_at=clock_timestamp() WHERE run_id=%s",
                    (run_id,),
                )
            # Logical cancellation never waits for an uninterruptible provider.
            cur.execute(
                """DELETE FROM agent_chat_interrupts WHERE config->>'managed_run_id'=%s
                   OR (session_id=%s AND NOT config ? 'managed_run_id')""",
                (run_id, run.get("session_id")),
            )
            cur.execute(
                """UPDATE agent_runs SET status='CANCELLED', version=version+1,
                   error_code='AGENT_RUN_CANCELLED_BY_USER', updated_at=NOW(),completed_at=NOW()
                   WHERE id=%s RETURNING *""", (run_id,),
            )
            updated = _row_to_dict(cur, cur.fetchone())
            event = append_event_with_cursor(cur, run_id, int(updated["version"]), "done", {
                "status": "cancelled", "run_id": run_id, "message": "用户已取消本轮 Agent 工作流",
            })
            cur.execute(
                """UPDATE agent_run_executions SET state='CANCELLED',
                   cancel_requested_at=COALESCE(cancel_requested_at,clock_timestamp()),
                   lease_until=NULL,terminal_event_id=%s,updated_at=clock_timestamp() WHERE run_id=%s""",
                (event["event_id"], run_id),
            )
            from app.domains.agent_run.execution_jobs import finish_job_with_cursor

            finish_job_with_cursor(cur, run_id, "CANCELLED", error=None)
            cur.execute(
                """UPDATE agent_turns SET status='cancelled',completed_at=NOW(),
                   assistant_message='用户已取消本轮 Agent 工作流',response='{"status":"cancelled"}'::jsonb
                   WHERE run_id=%s AND status IN ('received','running','waiting_approval')""", (run_id,),
            )
            if run.get("session_id"):
                from app.domains.outbox.repo import enqueue_event

                cur.execute("UPDATE agent_sessions SET version=version+1,updated_at=NOW() WHERE id=%s", (run["session_id"],))
                enqueue_event(cur, "agent.chat.completed", "agent_run", run_id, {"session_id": run["session_id"]})
        return updated

    def reconcile_expired(
        self,
        batch_size: int = 100,
        *,
        exclude_run_type: str | None = None,
    ) -> list[str]:
        """Pause expired attempts without automatically replaying side effects.

        Safe to run on startup or via an operator command while other API
        workers remain alive: an unexpired lease is never disturbed.
        """
        if batch_size < 1:
            raise ValueError("batch_size 必须大于 0")
        run_type_filter = "AND r.run_type <> %s" if exclude_run_type else ""
        params: tuple[Any, ...] = (batch_size,)
        if exclude_run_type:
            params = (exclude_run_type, batch_size)
        with get_cursor() as (_, cur):
            cur.execute(
                f"""SELECT r.id,r.version FROM agent_runs r JOIN agent_run_executions e ON e.run_id=r.id
                   WHERE e.state IN ('RUNNING','QUEUED') AND e.lease_until <= clock_timestamp()
                   AND r.status NOT IN ('COMPLETED','PARTIAL','NEEDS_REVIEW','ACTION_FAILED','FAILED','CANCELLED','ROLLBACK_FROZEN')
                   {run_type_filter}
                   ORDER BY e.lease_until LIMIT %s FOR UPDATE OF r SKIP LOCKED""",
                params,
            )
            expired = cur.fetchall()
            paused = []
            for run_id, version in expired:
                execution = lock_execution(cur, str(run_id))
                if not execution or execution["lease_valid"] or execution["state"] not in {ExecutionState.RUNNING, ExecutionState.QUEUED}:
                    continue
                cur.execute(
                    """UPDATE agent_run_executions SET state='PAUSED',pause_reason='interrupted',
                       fencing_token=fencing_token+1,owner_id=NULL,lease_until=NULL,checkpoint='{}'::jsonb,updated_at=clock_timestamp()
                       WHERE run_id=%s""", (run_id,),
                )
                from app.domains.agent_run.execution_jobs import finish_job_with_cursor

                finish_job_with_cursor(cur, str(run_id), "PAUSED", error="interrupted")
                append_event_with_cursor(cur, str(run_id), int(version), "workflow_status", {
                    "run_id": str(run_id), "status": "stopped", "stage": "decision",
                    "message": "执行进程中断，已保留结果，恢复前需要核对执行检查点",
                })
                frozen = execution.get("execution_input") or {}
                if frozen:
                    metadata = {"managed_run_id": str(run_id), "configurable": {"thread_id": str(run_id)}}
                    cur.execute(
                        """INSERT INTO agent_chat_interrupts(session_id,user_id,config,mode,user_message,status)
                           VALUES(%s,%s,%s,%s,%s,'pending') ON CONFLICT(session_id) DO UPDATE SET
                           config=EXCLUDED.config,mode=EXCLUDED.mode,user_message=EXCLUDED.user_message,
                           status='pending',claim_token=NULL,claimed_at=NULL,updated_at=NOW()""",
                        (frozen["session_id"], frozen["user_id"], Json(metadata), frozen["runtime"], frozen["user_message"]),
                    )
                paused.append(str(run_id))
        return paused

    def finish(
        self, permit: ExecutionPermit, *, snapshot: dict[str, Any], answer_text: str,
        events: list[tuple[str, dict[str, Any]]], next_context: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Commit the answer and the complete public tail as one transaction."""
        from app.domains.outbox.repo import enqueue_event

        answer_status = str((snapshot.get("answer") or {}).get("status") or snapshot.get("status") or "failed")
        state = ExecutionState.FAILED if answer_status == "failed" else ExecutionState.SUCCEEDED
        quality = {"completed": "complete"}.get(answer_status, answer_status)
        status = public_status(state, quality)
        with get_cursor() as (_, cur):
            # Always acquire Session before Run when both will be changed.
            cur.execute("SELECT session_id FROM agent_runs WHERE id=%s", (permit.run_id,))
            row = cur.fetchone()
            if row and row[0]:
                cur.execute("SELECT id FROM agent_sessions WHERE id=%s FOR UPDATE", (row[0],))
            run = lock_run(cur, permit.run_id)
            require_active(cur, run, permit)
            cur.execute(
                """UPDATE agent_runs SET status=%s,requirement=%s,version=version+1,
                   completed_at=NOW(),updated_at=NOW() WHERE id=%s RETURNING *""",
                (status, Json(snapshot), permit.run_id),
            )
            updated = _row_to_dict(cur, cur.fetchone())
            cur.execute(
                """UPDATE agent_turns SET status=%s,response=%s,assistant_message=%s,completed_at=NOW()
                   WHERE run_id=%s""",
                ("failed" if state == ExecutionState.FAILED else "completed" if quality == "complete" else "partial",
                 Json(snapshot), answer_text, permit.run_id),
            )
            if next_context is not None and run.get("session_id"):
                cur.execute(
                    """UPDATE agent_sessions SET state=jsonb_set(state,'{execution_context}',%s),
                       version=version+1,updated_at=NOW() WHERE id=%s""",
                    (Json(next_context), run["session_id"]),
                )
            elif run.get("session_id"):
                cur.execute("UPDATE agent_sessions SET version=version+1,updated_at=NOW() WHERE id=%s", (run["session_id"],))
            committed = [append_event_with_cursor(cur, permit.run_id, int(updated["version"]), name, payload)
                         for name, payload in events]
            if not committed or committed[-1]["event_type"] not in {"done", "error"}:
                raise ValueError("最终事件必须是 done 或 error")
            cur.execute(
                """UPDATE agent_run_executions SET state=%s,quality=%s,terminal_event_id=%s,
                   lease_until=NULL,updated_at=clock_timestamp() WHERE run_id=%s""",
                (state.value, quality, committed[-1]["event_id"], permit.run_id),
            )
            from app.domains.agent_run.execution_jobs import finish_job_with_cursor

            finish_job_with_cursor(
                cur,
                permit.run_id,
                "SUCCEEDED" if state == ExecutionState.SUCCEEDED else "FAILED",
                error=None if state == ExecutionState.SUCCEEDED else answer_text,
            )
            if projection is not None:
                enqueue_event(cur, "agent.chat.completed", "agent_run", permit.run_id, projection)
        return committed


run_coordinator = RunCoordinator()
