"""Independent scheduler entry: compute due periods and enqueue only."""

from __future__ import annotations

import argparse
import asyncio
import signal
import threading
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.domains.scheduling import repo

logger = get_logger(__name__)

_STOP = False
_STOP_EVENT: asyncio.Event | None = None
_LOOP: asyncio.AbstractEventLoop | None = None
_SIGNAL_LOCK = threading.Lock()


def _request_stop(*_: Any) -> None:
    global _STOP
    with _SIGNAL_LOCK:
        if _STOP:
            return
        _STOP = True
    if _STOP_EVENT is not None and _LOOP is not None:
        try:
            _LOOP.call_soon_threadsafe(_STOP_EVENT.set)
        except RuntimeError:
            pass


def run_scheduler_once(batch_size: int = 20) -> dict[str, Any]:
    """Scan one fairness batch; never execute a business task."""
    if batch_size < 1:
        raise ValueError("batch_size 必须为正整数")
    counts = {"scanned": 0, "created": 0, "existing": 0, "blocked": 0, "errors": 0}
    for schedule_id, target_id in repo.list_due_schedules(batch_size=batch_size):
        counts["scanned"] += 1
        try:
            job = repo.enqueue_due_schedule(schedule_id, target_id)
        except Exception as exc:  # one bad plan must not stop the batch
            logger.exception(
                "scheduled_period_failed", schedule_id=schedule_id, target_id=target_id
            )
            counts["errors"] += 1
            try:
                repo.mark_schedule_checked(schedule_id, target_id)
            except Exception:
                logger.exception(
                    "scheduled_cursor_failed", schedule_id=schedule_id, target_id=target_id
                )
            continue
        if job is None:
            continue
        if job["status"] == "BLOCKED_POLICY":
            counts["blocked"] += 1
        elif job["created"]:
            counts["created"] += 1
        else:
            counts["existing"] += 1
    return counts


async def _run_forever(poll_seconds: float = 5.0) -> None:
    global _STOP_EVENT, _LOOP
    if poll_seconds <= 0:
        raise ValueError("轮询间隔必须为正数")
    _STOP_EVENT = asyncio.Event()
    _LOOP = asyncio.get_running_loop()
    logger.info("scheduler_worker_started", poll_seconds=poll_seconds)
    try:
        while not _STOP:
            try:
                result = await asyncio.to_thread(run_scheduler_once)
            except Exception:
                # A transient database outage must not permanently kill the
                # role; no job state is inferred from the failed batch.
                logger.exception("scheduler_batch_failed")
            else:
                if any(result.values()):
                    logger.info("scheduler_batch_finished", **result)
            if _STOP:
                break
            try:
                await asyncio.wait_for(_STOP_EVENT.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        _STOP_EVENT = None
        _LOOP = None
    logger.info("scheduler_worker_stopped")


def _assert_role_available() -> None:
    """Refuse overlapping ownership of the same business cron."""
    migrated = {
        item.strip() for item in settings.SCHEDULER_MIGRATED_KINDS.split(",")
        if item.strip()
    }
    if settings.SCHEDULER_ROLE_ENABLED and not migrated:
        raise RuntimeError(
            "API 内业务 scheduler 仍启用；请把已迁移任务写入 "
            "SCHEDULER_MIGRATED_KINDS，或设置 SCHEDULER_ROLE_ENABLED=false"
        )


def main() -> None:
    _assert_role_available()
    parser = argparse.ArgumentParser(description="Durable schedule enqueue worker")
    parser.add_argument("--once", action="store_true", help="scan one batch and exit")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    if args.once:
        import json

        print(json.dumps(run_scheduler_once(args.batch_size), ensure_ascii=False), flush=True)
        return
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    asyncio.run(_run_forever(args.poll_seconds))


if __name__ == "__main__":
    main()
