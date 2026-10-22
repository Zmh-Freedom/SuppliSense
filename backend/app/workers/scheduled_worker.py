"""Lease-driven execution of durable scheduled jobs in its own process."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import threading
import uuid
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.domains.scheduling import repo
from app.domains.scheduling.executor import execute_scheduled_job

logger = get_logger(__name__)
DRAIN_TIMEOUT_SECONDS = 30.0

_STOP = False
_STOP_EVENT: asyncio.Event | None = None
_EXIT_TIMER: threading.Timer | None = None
_CLI_PROCESS = False


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _request_stop(*_: Any) -> None:
    global _STOP, _EXIT_TIMER
    if _STOP:
        return
    _STOP = True
    if _STOP_EVENT is not None:
        _STOP_EVENT.set()
    if _CLI_PROCESS:
        # A synchronous provider call cannot be cancelled by cancelling its
        # asyncio future. The deadline guarantees process exit; the lease
        # remains for a takeover that verifies committed results.
        _EXIT_TIMER = threading.Timer(DRAIN_TIMEOUT_SECONDS, os._exit, args=(0,))
        _EXIT_TIMER.daemon = True
        _EXIT_TIMER.start()


async def _heartbeat_until_done(
    job: dict[str, Any], worker_id: str, worker: asyncio.Task
) -> None:
    interval = settings.SCHEDULER_WORKER_HEARTBEAT_SECONDS
    lease = settings.SCHEDULER_WORKER_LEASE_SECONDS
    while not worker.done():
        await asyncio.sleep(interval)
        if worker.done():
            return
        ok = await asyncio.to_thread(
            repo.heartbeat_scheduled_job,
            job["id"], worker_id, int(job["fencing_token"]), lease_seconds=lease,
        )
        if not ok:
            logger.warning("scheduled_job_lease_lost", job_id=job["id"], worker_id=worker_id)
            return


async def run_scheduled_worker_once_async(worker_id: str | None = None) -> dict[str, Any]:
    worker_id = worker_id or _default_worker_id()
    job = await asyncio.to_thread(
        repo.claim_scheduled_job,
        worker_id,
        lease_seconds=settings.SCHEDULER_WORKER_LEASE_SECONDS,
    )
    if job is None:
        return {"executed": 0, "worker_id": worker_id}
    worker = asyncio.create_task(
        asyncio.to_thread(execute_scheduled_job, job, worker_id),
        name=f"scheduled-job:{job['id']}",
    )
    heartbeat = asyncio.create_task(
        _heartbeat_until_done(job, worker_id, worker),
        name=f"scheduled-heartbeat:{job['id']}",
    )
    try:
        result = await worker
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
    return {"executed": 1, "worker_id": worker_id, **result}


def run_scheduled_worker_once(worker_id: str | None = None) -> dict[str, Any]:
    return asyncio.run(run_scheduled_worker_once_async(worker_id))


async def _run_forever(worker_id: str) -> None:
    global _STOP_EVENT
    if settings.SCHEDULER_WORKER_LEASE_SECONDS < 3 * settings.SCHEDULER_WORKER_HEARTBEAT_SECONDS:
        raise ValueError("调度执行租约必须至少为心跳间隔的 3 倍")
    _STOP_EVENT = asyncio.Event()
    logger.info(
        "scheduled_worker_started",
        worker_id=worker_id,
        poll_seconds=settings.SCHEDULER_POLL_SECONDS,
    )
    try:
        while not _STOP:
            try:
                result = await run_scheduled_worker_once_async(worker_id)
            except Exception:
                logger.exception("scheduled_worker_batch_failed", worker_id=worker_id)
                result = {"executed": 0}
            if result.get("executed"):
                logger.info("scheduled_job_finished", **{k: v for k, v in result.items() if k != "worker_id"})
            elif not _STOP:
                try:
                    await asyncio.wait_for(
                        _STOP_EVENT.wait(), timeout=settings.SCHEDULER_POLL_SECONDS
                    )
                except asyncio.TimeoutError:
                    pass
    finally:
        _STOP_EVENT = None
    logger.info("scheduled_worker_stopped", worker_id=worker_id)


def main() -> None:
    global _CLI_PROCESS
    import json

    parser = argparse.ArgumentParser(description="Durable scheduled job worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id", default="")
    args = parser.parse_args()
    worker_id = args.worker_id or _default_worker_id()
    if args.once:
        print(json.dumps(run_scheduled_worker_once(worker_id), ensure_ascii=False, default=str), flush=True)
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
