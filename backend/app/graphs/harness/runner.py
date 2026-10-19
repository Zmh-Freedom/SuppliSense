"""Keep graph producers alive independently of HTTP subscribers."""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger()
_END = object()


@dataclass(eq=False)
class _Run:
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    finished: bool = False


_runs: dict[tuple[asyncio.AbstractEventLoop, str], _Run] = {}


async def managed_stream(key: str, source: Callable[[], AsyncIterator[str]]) -> AsyncIterator[str]:
    """A full subscriber queue disconnects that subscriber; PG retains replay."""
    identity = (asyncio.get_running_loop(), key)
    run = _runs.get(identity)
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    if run is None:
        run = _Run()
        _runs[identity] = run
        run.subscribers.add(queue)

        async def produce() -> None:
            try:
                async for event in source():
                    for subscriber in list(run.subscribers):
                        if subscriber.full():
                            run.subscribers.discard(subscriber)
                            while not subscriber.empty():
                                subscriber.get_nowait()
                            subscriber.put_nowait(RuntimeError("事件订阅落后，请重新连接恢复结果"))
                            continue
                        subscriber.put_nowait(event)
            except Exception as exc:
                logger.exception("harness_producer_failed", run_id=key, error=type(exc).__name__)
                for subscriber in run.subscribers:
                    if not subscriber.full():
                        subscriber.put_nowait(exc)
            finally:
                run.finished = True
                for subscriber in run.subscribers:
                    if not subscriber.full():
                        subscriber.put_nowait(_END)
                _runs.pop(identity, None)

        run.task = asyncio.create_task(produce(), name=f"harness:{key}")
    else:
        raise RuntimeError("该任务已有活动订阅，请使用事件回放接口")
    try:
        while True:
            if run.finished and queue.empty():
                return
            item = await queue.get()
            if item is _END:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        run.subscribers.discard(queue)


async def stop_managed_runners() -> None:
    """Application shutdown only; cancellation is recorded by the producer."""
    tasks = [run.task for (loop, _), run in list(_runs.items())
             if loop is asyncio.get_running_loop() and run.task is not None]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def reconcile_interrupted_runs() -> None:
    """Each API may scan; Run row locks make concurrent scans harmless."""
    from app.domains.agent_run.coordinator import run_coordinator

    while True:
        await asyncio.sleep(5)
        try:
            await asyncio.to_thread(run_coordinator.reconcile_expired)
        except Exception as exc:
            logger.warning("harness_reconciliation_failed", error=type(exc).__name__)
