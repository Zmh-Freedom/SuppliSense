"""Explicit recovery of read-only Harness runs from verified saved state."""

import asyncio
from typing import Any, AsyncIterator

from app.domains.agent_run.coordinator import ExecutionLost, run_coordinator
from app.domains.agent_run.chat_interrupt_repo import ack_chat_interrupt, release_chat_interrupt
from app.graphs.checkpoint_recovery import checkpoint_proof, reconcile_checkpoint
from app.graphs.harness.runner import managed_stream


async def stream_harness_recovery(run_id: str, user_id: str, saver: Any, *, approved: bool, claim: str | None) -> AsyncIterator[str]:
    subscription = managed_stream(run_id, lambda: _recover(run_id, user_id, saver, approved, claim))
    try:
        async for item in subscription:
            yield item
    finally:
        await subscription.aclose()


async def _recover(run_id, user_id, saver, approved, claim):
    from app.graphs.harness.graph import build_harness_graph
    from app.graphs.agent_supervisor.runtime import _bind_recovered_checkpoint
    from app.graphs.streaming import _produce_harness_events, _sse_event

    record = await asyncio.to_thread(run_coordinator.recovery_record, run_id, user_id)
    frozen = record["execution_input"]
    session_id = frozen["session_id"]
    try:
        if not approved:
            await asyncio.to_thread(run_coordinator.cancel, run_id, user_id)
            from app.domains.agent_run.repo import list_events_after
            for item in await asyncio.to_thread(list_events_after, run_id):
                if item["event_type"] == "done":
                    yield _sse_event(item["event_type"], item["payload"], item["event_id"])
            return
        config = {"configurable": {"thread_id": run_id, "checkpoint_ns": ""}}
        graph = build_harness_graph(checkpointer=saver)
        checkpoint = await graph.aget_state(config)
        values = reconcile_checkpoint(record, dict(checkpoint.values or {}))
        proof = checkpoint_proof(checkpoint)
        config = dict(checkpoint.config)
        if record["checkpoint"] and record["checkpoint"] != proof:
            raise ExecutionLost("检查点已变化，需要人工核对")
        if not record["checkpoint"]:
            await asyncio.to_thread(_bind_recovered_checkpoint, run_id, user_id, proof)
        permit = await asyncio.to_thread(run_coordinator.resume, run_id, user_id, checkpoint=proof)
        if values != dict(checkpoint.values or {}):
            config = await graph.aupdate_state(config, values)
        async for item in _produce_harness_events(
            frozen["user_message"], session_id, frozen["execution_context"], user_id=user_id,
            run_config=config, checkpointer=saver, turn_id=frozen["turn_id"], run_id=run_id,
            resume_permit=permit, resume_state=values,
        ):
            yield item
        if claim:
            await asyncio.to_thread(ack_chat_interrupt, session_id, claim)
    except Exception as exc:
        if claim:
            await asyncio.to_thread(release_chat_interrupt, session_id, claim)
        yield _sse_event("error", {"message": str(exc) if isinstance(exc, (ExecutionLost, PermissionError)) else "恢复失败，任务仍保留，请稍后核对。"})
