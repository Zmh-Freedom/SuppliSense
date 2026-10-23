"""Lease-driven execution of the existing Harness in an independent process."""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import uuid
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.domains.agent_run import execution_jobs
from app.domains.agent_run.coordinator import ExecutionLost, RunCoordinator, run_coordinator
from app.domains.agent_run.repo import get_run

logger = get_logger(__name__)


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


async def _get_checkpointer():
    """Resolve the persistent saver lazily so the worker loop owns its event loop."""
    from app.graphs.sourcing_risk_v2.checkpointer import get_sourcing_risk_checkpointer

    return await get_sourcing_risk_checkpointer()


async def _prepare_resume(
    claimed: dict[str, Any],
    record: dict[str, Any],
    checkpointer: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return the graph config and verified state for a takeover attempt."""
    run_id = str(claimed["permit"].run_id)
    config = {"configurable": {"thread_id": run_id, "checkpoint_ns": ""}}
    if not claimed.get("recovery"):
        return config, None

    from app.graphs.harness.graph import build_harness_graph

    graph = build_harness_graph(checkpointer=checkpointer)
    checkpoint = await graph.aget_state(config)
    values = dict(checkpoint.values or {})
    if not values:
        # No checkpoint means there is nothing to resume. A Run that already
        # persisted a snapshot must not silently restart and replay tools.
        if record.get("snapshot") or record.get("checkpoint"):
            raise ExecutionLost("检查点缺失，需要人工核对")
        # The previous process died before LangGraph wrote any checkpoint.
        # Fresh execution is safe for this read-only Harness.
        return config, None

    from app.graphs.checkpoint_recovery import checkpoint_proof, reconcile_checkpoint

    proof = checkpoint_proof(checkpoint)
    if record.get("checkpoint") and record["checkpoint"] != proof:
        raise ExecutionLost("检查点已变化，需要人工核对")
    reconciled = reconcile_checkpoint(record, values)
    if record.get("checkpoint"):
        # A pause proof authorizes this resume once. Later crash recovery must
        # accept the newer checkpoint written by this fenced attempt.
        await asyncio.to_thread(
            run_coordinator.consume_recovery_checkpoint,
            claimed["permit"],
            proof,
        )
    if reconciled != values:
        config = await graph.aupdate_state(config, reconciled)
    return config, reconciled


async def _heartbeat_until_done(
    *,
    job_id: str,
    worker_id: str,
    permit,
    producer: asyncio.Task,
) -> None:
    """Renew both leases until the producer exits or ownership is lost."""
    interval = settings.AGENT_WORKER_HEARTBEAT_SECONDS
    lease_seconds = settings.AGENT_WORKER_LEASE_SECONDS
    coordinator = RunCoordinator(lease_seconds=lease_seconds)
    while not producer.done():
        await asyncio.sleep(interval)
        if producer.done():
            return
        job_ok = await asyncio.to_thread(
            execution_jobs.heartbeat_job, job_id, worker_id, lease_seconds=lease_seconds
        )
        execution_ok = await asyncio.to_thread(coordinator.heartbeat, permit)
        if not job_ok or not execution_ok:
            producer.cancel()
            return


async def _heartbeat_candidate_review_until_done(
    *,
    batch_id: str,
    worker_id: str,
    fencing_token: int,
    producer: asyncio.Task,
) -> None:
    """Renew the review-batch lease and stop graph work after fencing."""
    interval = settings.AGENT_WORKER_HEARTBEAT_SECONDS
    lease_seconds = settings.AGENT_WORKER_LEASE_SECONDS
    while not producer.done():
        await asyncio.sleep(interval)
        if producer.done():
            return
        lease_ok = await asyncio.to_thread(
            execution_jobs.heartbeat_candidate_review_batch,
            batch_id,
            worker_id,
            fencing_token,
            lease_seconds=lease_seconds,
        )
        if not lease_ok:
            producer.cancel()
            return


async def _execute_candidate_review_batch(
    claimed: dict[str, Any], worker_id: str,
) -> dict[str, Any]:
    """Resume the sourcing graph through its durable interrupt checkpoint."""
    from app.graphs.sourcing_risk_v2.runner import resume_candidate_review_batch

    batch_id = str(claimed["batch_id"])
    fencing_token = int(claimed["fencing_token"])
    producer = asyncio.create_task(
        resume_candidate_review_batch(batch_id),
        name=f"candidate-review:{batch_id}",
    )
    heartbeat = asyncio.create_task(
        _heartbeat_candidate_review_until_done(
            batch_id=batch_id,
            worker_id=worker_id,
            fencing_token=fencing_token,
            producer=producer,
        ),
        name=f"candidate-review-heartbeat:{batch_id}",
    )
    try:
        outcome = await producer
    except asyncio.CancelledError:
        return {"terminal": False, "lease_lost": True, "candidate_batch": {"status": "lease_lost"}}
    except Exception as exc:
        logger.exception(
            "candidate_review_resume_failed",
            batch_id=batch_id,
            worker_id=worker_id,
        )
        # Keep the lease until expiry. The next worker can recover the same PG
        # intent and LangGraph checkpoint without marking the batch consumed.
        return {
            "terminal": False,
            "candidate_batch": {"status": "retry_pending", "error": str(exc) or type(exc).__name__},
        }
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        if not producer.done():
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    lease_lost = outcome.get("status") == "lease_lost"
    return {
        "terminal": not lease_lost,
        "lease_lost": lease_lost,
        "candidate_batch": outcome,
    }


async def _finish_failed(permit, run_id: str, message: str, frozen: dict[str, Any]) -> None:
    failures = [
        (
            "workflow_status",
            {
                "run_id": run_id,
                "status": "failed",
                "stage": "decision",
                "message": "Harness 工作流执行失败",
            },
        ),
        ("error", {"run_id": run_id, "message": message}),
    ]
    await asyncio.to_thread(
        run_coordinator.finish,
        permit,
        snapshot={**frozen, "status": "failed"},
        answer_text=message,
        events=failures,
    )


async def _execute_claimed_job(
    claimed: dict[str, Any],
    worker_id: str,
) -> dict[str, Any]:
    from app.graphs.streaming import _produce_harness_events

    permit = claimed["permit"]
    run_id = str(permit.run_id)
    run = await asyncio.to_thread(get_run, run_id)
    if run is None:
        raise ExecutionLost("Agent Run 不存在")
    user_id = str(run.get("user_id") or "")
    if not user_id:
        raise ExecutionLost("Agent Run 缺少操作者")

    record = await asyncio.to_thread(run_coordinator.recovery_record, run_id, user_id)
    frozen = dict(record["execution_input"])
    if frozen.get("runtime") != "harness":
        # Supervisor write-approval remains on its compatibility entry in 3A.
        await asyncio.to_thread(execution_jobs.requeue_job_with_cursor, run_id)
        return {"terminal": False, "skipped": "non_harness_runtime"}

    checkpointer = await _get_checkpointer()
    config, resume_state = await _prepare_resume(claimed, record, checkpointer)
    await asyncio.to_thread(run_coordinator.check, permit)

    async def consume() -> None:
        async for _ in _produce_harness_events(
            frozen["user_message"],
            frozen["session_id"],
            frozen.get("execution_context") or {},
            user_id=user_id,
            run_config=config,
            checkpointer=checkpointer,
            turn_id=frozen.get("turn_id"),
            run_id=run_id,
            resume_permit=permit,
            resume_state=resume_state,
        ):
            pass

    producer = asyncio.create_task(consume(), name=f"agent-job:{run_id}")
    heartbeat = asyncio.create_task(
        _heartbeat_until_done(
            job_id=claimed["job_id"],
            worker_id=worker_id,
            permit=permit,
            producer=producer,
        ),
        name=f"agent-heartbeat:{run_id}",
    )
    try:
        await producer
    except ExecutionLost:
        return {"terminal": False, "lease_lost": True}
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        try:
            await _finish_failed(permit, run_id, message, frozen)
        except ExecutionLost:
            return {"terminal": False, "lease_lost": True}
        logger.exception("agent_worker_job_failed", run_id=run_id, worker_id=worker_id)
        return {"terminal": True, "failed": True, "error": message}
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        if not producer.done():
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    current = await asyncio.to_thread(get_run, run_id)
    terminal = bool(current and current["status"] in {
        "COMPLETED", "PARTIAL", "NEEDS_REVIEW", "ACTION_FAILED", "FAILED", "CANCELLED"
    })
    if not terminal:
        message = "执行进程未提交终态"
        try:
            await _finish_failed(permit, run_id, message, frozen)
        except ExecutionLost:
            return {"terminal": False, "lease_lost": True}
        return {"terminal": True, "failed": True, "error": message}
    return {"terminal": True, "failed": current["status"] in {"FAILED", "CANCELLED"}}


async def run_worker_once_async(worker_id: str | None = None) -> dict[str, Any]:
    """Claim and execute at most one durable job in the current event loop."""
    worker_id = worker_id or _default_worker_id()
    review_batch = await asyncio.to_thread(
        execution_jobs.claim_candidate_review_batch,
        worker_id,
        lease_seconds=settings.AGENT_WORKER_LEASE_SECONDS,
    )
    if review_batch is not None:
        if review_batch.get("status") != "processing":
            return {
                "executed": 1,
                "worker_id": worker_id,
                "terminal": review_batch.get("status") in {"cancelled", "stale", "failed"},
                "candidate_batch": review_batch,
            }
        try:
            outcome = await _execute_candidate_review_batch(review_batch, worker_id)
        except Exception as exc:
            logger.exception("candidate_review_worker_failed", worker_id=worker_id)
            return {
                "executed": 1,
                "worker_id": worker_id,
                "terminal": False,
                "candidate_batch": {"status": "retry_pending", "error": str(exc) or type(exc).__name__},
            }
        return {"executed": 1, "worker_id": worker_id, **outcome}

    claimed = await asyncio.to_thread(
        execution_jobs.claim_job,
        worker_id,
        lease_seconds=settings.AGENT_WORKER_LEASE_SECONDS,
        max_recoveries=settings.AGENT_WORKER_MAX_RECOVERIES,
    )
    if claimed is None:
        return {"executed": 0, "worker_id": worker_id}
    try:
        outcome = await _execute_claimed_job(claimed, worker_id)
    except ExecutionLost as exc:
        # Ownership disappeared (or a checkpoint needs review). Leave the job
        # LEASED so expiry/takeover handles it; failing the job here would
        # strand a Run that another worker may still be able to recover.
        logger.warning("agent_worker_lease_lost", worker_id=worker_id, error=str(exc))
        return {
            "executed": 1,
            "worker_id": worker_id,
            "terminal": False,
            "lease_lost": True,
            "error": str(exc),
        }
    except Exception as exc:
        logger.exception("agent_worker_claim_failed", worker_id=worker_id)
        message = str(exc) or type(exc).__name__
        try:
            await _finish_failed(claimed["permit"], str(claimed["permit"].run_id), message, {})
        except Exception as commit_error:
            logger.exception("agent_worker_failure_commit_failed", worker_id=worker_id)
            # Only Coordinator may atomically close the Run and its job. An
            # uncertain or fenced-out commit must remain recoverable.
            return {
                "executed": 1,
                "worker_id": worker_id,
                "terminal": False,
                "lease_lost": isinstance(commit_error, ExecutionLost),
                "error": message,
            }
        return {
            "executed": 1,
            "worker_id": worker_id,
            "terminal": True,
            "failed": True,
            "error": message,
        }
    return {"executed": 1, "worker_id": worker_id, **outcome}


def run_worker_once(worker_id: str | None = None) -> dict[str, Any]:
    """Synchronous convenience entry used by scripts and integration tests."""
    return asyncio.run(run_worker_once_async(worker_id))


_STOP = False
DRAIN_TIMEOUT_SECONDS = 30.0
_STOP_EVENT: asyncio.Event | None = None
_EXIT_TIMER: threading.Timer | None = None
_CLI_PROCESS = False


def _request_stop(*_: Any) -> None:
    global _STOP, _EXIT_TIMER
    if _STOP:
        return
    _STOP = True
    if _STOP_EVENT is not None:
        _STOP_EVENT.set()
    if _CLI_PROCESS:
        # asyncio.run also joins its thread executor. A stuck synchronous
        # provider cannot be cancelled by cancelling its asyncio future.
        # The process deadline stops renewals even in that case; leave the
        # durable attempt leased so another owner can recover after expiry.
        _EXIT_TIMER = threading.Timer(DRAIN_TIMEOUT_SECONDS, os._exit, args=(0,))
        _EXIT_TIMER.daemon = True
        _EXIT_TIMER.start()


async def _run_forever(worker_id: str) -> None:
    global _STOP_EVENT
    if settings.AGENT_WORKER_LEASE_SECONDS < 3 * settings.AGENT_WORKER_HEARTBEAT_SECONDS:
        raise ValueError("执行租约必须至少为心跳间隔的 3 倍")
    await _get_checkpointer()
    logger.info(
        "agent_worker_started",
        worker_id=worker_id,
        lease_seconds=settings.AGENT_WORKER_LEASE_SECONDS,
        heartbeat_seconds=settings.AGENT_WORKER_HEARTBEAT_SECONDS,
        poll_seconds=settings.AGENT_WORKER_POLL_SECONDS,
    )
    _STOP_EVENT = asyncio.Event()
    try:
        while not _STOP:
            work = asyncio.create_task(run_worker_once_async(worker_id))
            stop = asyncio.create_task(_STOP_EVENT.wait())
            try:
                await asyncio.wait({work, stop}, return_when=asyncio.FIRST_COMPLETED)
                if stop.done() and not work.done():
                    done, _ = await asyncio.wait({work}, timeout=DRAIN_TIMEOUT_SECONDS)
                    if not done:
                        work.cancel()
                        logger.warning("agent_worker_drain_expired", worker_id=worker_id)
                        return
                result = work.result()
            finally:
                stop.cancel()
                await asyncio.gather(stop, return_exceptions=True)
                if not work.done():
                    work.cancel()
            if result.get("executed"):
                logger.info(
                    "agent_worker_job_finished",
                    worker_id=worker_id,
                    terminal=bool(result.get("terminal")),
                    failed=bool(result.get("failed")),
                    lease_lost=bool(result.get("lease_lost")),
                )
            elif not _STOP:
                try:
                    await asyncio.wait_for(_STOP_EVENT.wait(), timeout=settings.AGENT_WORKER_POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass
    finally:
        _STOP_EVENT = None
    logger.info("agent_worker_stopped", worker_id=worker_id)


def main() -> None:
    global _CLI_PROCESS
    import argparse
    import json
    import signal

    parser = argparse.ArgumentParser(description="Durable Agent Harness worker")
    parser.add_argument("--once", action="store_true", help="claim and run at most one job")
    parser.add_argument("--worker-id", default="")
    args = parser.parse_args()
    worker_id = args.worker_id or _default_worker_id()
    if args.once:
        result = run_worker_once(worker_id)
        print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
        return
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    _CLI_PROCESS = True
    try:
        asyncio.run(_run_forever(worker_id))
    finally:
        if _EXIT_TIMER is not None:
            _EXIT_TIMER.cancel()
        _CLI_PROCESS = False


if __name__ == "__main__":
    main()
