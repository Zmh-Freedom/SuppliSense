"""Managed Supervisor execution; HTTP readers only subscribe to durable events."""

import asyncio
import time
from typing import Any, AsyncIterator
from uuid import uuid4

from langgraph.types import Command

from app.domains.agent_run.coordinator import ExecutionLost, execution_scope, run_coordinator
from app.domains.agent_run.state_store import session_state_store
from app.graphs.checkpoint_recovery import checkpoint_proof, input_digest, validate_checkpoint
from app.graphs.harness.runner import managed_stream

HEARTBEAT_SECONDS = 5

async def stream_managed_supervisor(
    graph: Any, run_id: str, user_id: str, *, approved: bool | None = None,
    interrupt_claim: str | None = None,
) -> AsyncIterator[str]:
    subscription = managed_stream(run_id, lambda: _produce(graph, run_id, user_id, approved, interrupt_claim))
    try:
        async for event in subscription:
            yield event
    finally:
        await subscription.aclose()


async def _produce(graph, run_id, user_id, approved, interrupt_claim):
    from app.graphs.agent_core.adapter import build_agent_supervisor_graph_input, collect_supplier_references
    from app.graphs.streaming import _sse_event, _supervisor_interrupt_data, _SUPERVISOR_STAGE_MESSAGES
    from app.domains.agent_run.chat_interrupt_repo import ack_chat_interrupt, release_chat_interrupt

    record = await asyncio.to_thread(run_coordinator.recovery_record, run_id, user_id)
    frozen = record["execution_input"]
    session_id = frozen["session_id"]
    config = {"configurable": {"thread_id": run_id, "checkpoint_ns": ""}}
    permit = None
    queue = asyncio.Queue()
    paused = False
    worker = None

    async def event(name, payload):
        item = await asyncio.to_thread(session_state_store.append_harness_event, run_id, name, payload, permit=permit)
        await queue.put(_sse_event(name, payload, item["event_id"]))

    try:
        if approved is not None:
            checkpoint = await graph.aget_state(config)
            validate_checkpoint(record, dict(checkpoint.values or {}))
            proof = checkpoint_proof(checkpoint)
            config = dict(checkpoint.config)
            if record["checkpoint"] and record["checkpoint"] != proof:
                raise ExecutionLost("审批检查点已变化，需要人工核对")
            await asyncio.to_thread(_validate_approvals, run_id, user_id, dict(checkpoint.values or {}))
            # Expired-process reconciliation has no proof until the durable saver
            # is examined. Bind it only while holding the paused execution lock.
            if not record["checkpoint"]:
                await asyncio.to_thread(_bind_recovered_checkpoint, run_id, user_id, proof)
            permit = await asyncio.to_thread(run_coordinator.resume, run_id, user_id, checkpoint=proof)
            # Keep Command intact. Never turn resume into a fresh user message.
            input_data = Command(resume={"approved": approved}) if checkpoint.tasks and any(task.interrupts for task in checkpoint.tasks) else None
        else:
            permit = await asyncio.to_thread(run_coordinator.claim, run_id, f"api:{uuid4()}")
            context = frozen["execution_context"]
            input_data = {
                **build_agent_supervisor_graph_input(context, run_id=run_id, user_message=frozen["user_message"]),
                "session_id": session_id, "user_id": user_id, "input_digest": input_digest(frozen),
                "execution_context": context,
            }
        await event("run", {"run_id": run_id, "turn_id": frozen["turn_id"]})

        async def execute():
            nonlocal paused
            with execution_scope(permit):
                async for update in graph.astream(input_data, config=config, stream_mode="updates"):
                    await asyncio.to_thread(run_coordinator.check, permit)
                    interrupt = _supervisor_interrupt_data(update)
                    if interrupt is not None:
                        paused = True
                        continue
                    for stage, output in update.items():
                        if not isinstance(output, dict):
                            continue
                        await event("workflow_status", {"run_id": run_id, "status": "running", "stage": "approval" if stage == "approval_gate" else "executing", "message": _SUPERVISOR_STAGE_MESSAGES.get(stage, "正在执行任务")})
                        if stage == "plan_task":
                            for task in (output.get("plan") or {}).get("tasks") or []:
                                await event("tool_call", {"tool": f"{task.get('agent', 'unknown')}_agent", "args": {"task_id": task.get("task_id"), "depends_on": task.get("depends_on", [])}})
                        if stage == "execute_ready_tasks":
                            for task_id, result in (output.get("agent_results") or {}).items():
                                await event("tool_result", {"tool": f"{result.get('agent', task_id)}_agent", "result": result})
                checkpoint = await graph.aget_state({"configurable": {"thread_id": run_id, "checkpoint_ns": ""}})
                snapshot = dict(checkpoint.values or {})
                latest_record = await asyncio.to_thread(run_coordinator.recovery_record, run_id, user_id)
                validate_checkpoint(latest_record, snapshot)
                if paused:
                    pending = snapshot.get("pending_approvals") or []
                    tail = [
                        ("workflow_status", {"run_id": run_id, "status": "waiting_approval", "stage": "approval", "message": "等待人工确认后继续执行"}),
                        ("approval_required", {"message": "确认此操作？", "tool": "agent_supervisor", "args": {}, "session_id": session_id, "requires_human_approval": True, "pending_approvals": pending}),
                        ("done", {"answer": "等待人工确认后继续执行", "status": "waiting_approval"}),
                    ]
                    committed = await asyncio.to_thread(run_coordinator.pause, permit, snapshot=snapshot, checkpoint=checkpoint_proof(checkpoint), reason="waiting_approval", events=tail)
                else:
                    text = str(snapshot.get("final_answer") or "任务已结束。")
                    refs = collect_supplier_references([], snapshot.get("agent_results") or {}, "Supervisor 已验证结果")
                    status = "partial" if snapshot.get("task_status") == "PARTIAL_COMPLETED" else "completed"
                    if snapshot.get("task_status") == "FAILED":
                        status = "failed"
                    snapshot["status"] = status
                    context = dict(frozen["execution_context"])
                    context["history"] = [*list(context.get("history") or []), {"role": "user", "content": frozen["user_message"]}, {"role": "assistant", "content": text}]
                    context["references"] = collect_supplier_references(list(context.get("references") or []), refs, "Supervisor 已验证结果")
                    tail = [("answer_chunk", {"text": text})]
                    if refs:
                        tail.append(("references", {"items": refs}))
                    tail.extend([("workflow_status", {"run_id": run_id, "status": status, "stage": "completed", "message": "本轮 Agent 工作流已完成"}), ("done", {"answer": text, "status": status})])
                    committed = await asyncio.to_thread(run_coordinator.finish, permit, snapshot=snapshot, answer_text=text, events=tail, next_context=context, projection={"session_id": session_id})
                for item in committed:
                    await queue.put(_sse_event(item["event_type"], item["payload"], item["event_id"]))

        worker = asyncio.create_task(execute())
        last_heartbeat = time.monotonic()
        while not worker.done() or not queue.empty():
            if not worker.done() and time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
                if not await asyncio.to_thread(run_coordinator.heartbeat, permit):
                    raise ExecutionLost("执行许可已失效")
                last_heartbeat = time.monotonic()
            try:
                yield await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                continue
        await worker
        if interrupt_claim:
            await asyncio.to_thread(ack_chat_interrupt, session_id, interrupt_claim)
    except (Exception, asyncio.CancelledError) as exc:
        if worker and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        if isinstance(exc, ExecutionLost):
            from app.domains.agent_run.repo import list_events_after, get_run
            from app.domains.agent_run.coordinator import PUBLIC_TERMINAL

            run = await asyncio.to_thread(get_run, run_id)
            if run and run["status"] in PUBLIC_TERMINAL:
                for item in await asyncio.to_thread(list_events_after, run_id):
                    if item["event_type"] in {"done", "error"} and item["payload"].get("status") != "waiting_approval":
                        yield _sse_event(item["event_type"], item["payload"], item["event_id"])
                return
        # Uncertain writes, lost checkpoints, or a process shutdown must never
        # become an implicit retry. Preserve the task for explicit inspection.
        if permit:
            try:
                checkpoint = await graph.aget_state(config)
                latest = await asyncio.to_thread(run_coordinator.recovery_record, run_id, user_id)
                committed = await asyncio.to_thread(run_coordinator.pause, permit, snapshot=latest["snapshot"], checkpoint=checkpoint_proof(checkpoint), reason="needs_review", events=[("workflow_status", {"run_id": run_id, "status": "stopped", "stage": "recovery", "message": "执行已暂停，恢复前需要核对检查点及操作回执"})])
                for item in committed:
                    yield _sse_event(item["event_type"], item["payload"], item["event_id"])
            except ExecutionLost:
                pass
        if interrupt_claim:
            await asyncio.to_thread(release_chat_interrupt, session_id, interrupt_claim)
        if isinstance(exc, asyncio.CancelledError):
            raise
        yield _sse_event("error", {"message": str(exc) if isinstance(exc, (ExecutionLost, PermissionError, ValueError)) else "执行已暂停，请核对操作回执后恢复。"})


def _bind_recovered_checkpoint(run_id: str, user_id: str, proof: dict) -> None:
    from psycopg2.extras import Json
    from app.db.postgres import get_cursor
    from app.domains.agent_run.coordinator import lock_run, lock_execution

    with get_cursor() as (_, cur):
        run = lock_run(cur, run_id)
        execution = lock_execution(cur, run_id)
        if str(run.get("user_id")) != user_id or not execution or execution["state"] != "PAUSED" or execution["checkpoint"]:
            raise ExecutionLost("恢复检查点已被其他执行者处理")
        cur.execute("UPDATE agent_run_executions SET checkpoint=%s WHERE run_id=%s", (Json(proof), run_id))


def _validate_approvals(run_id: str, user_id: str, values: dict) -> None:
    from app.domains.sourcing_risk.action_service import get_action_proposal_for_update
    from app.db.postgres import get_cursor

    with get_cursor() as (_, cur):
        cur.execute("SELECT role,is_active FROM users WHERE id=%s", (user_id,))
        actor = cur.fetchone()
        if not actor or not actor[1] or actor[0] not in {"admin", "purchaser", "analyst"}:
            raise PermissionError("当前用户没有审批权限")
        for pending in values.get("pending_approvals") or []:
            proposal = get_action_proposal_for_update(cur, run_id, pending["approval_id"])
            if proposal is None:
                raise ExecutionLost("检查点中的审批提案不存在")
            if proposal["status"] not in {"pending", "approved"}:
                raise ExecutionLost("审批提案已被拒绝或冻结")
            # Proposals already executed must have a recorded real receipt.
            cur.execute("SELECT status FROM agent_action_receipts WHERE proposal_id=%s", (proposal["id"],))
            receipt = cur.fetchone()
            if receipt and receipt[0] != "succeeded":
                raise ExecutionLost("操作执行结果未知，不能自动重试")
            metadata = (proposal.get("payload") or {}).get("_harness_action") or {}
            if not receipt:
                from datetime import datetime, timezone

                expires = metadata.get("expires_at") or pending.get("expires_at")
                if not expires or datetime.fromisoformat(str(expires)) <= datetime.now(timezone.utc):
                    raise ExecutionLost("审批提案已过期，请取消后重新发起")
            target = dict(pending.get("target") or {})
            target.pop("expires_at", None)
            if metadata:
                from app.graphs.harness.actions import build_action_hash, normalize_action_arguments
                expected = build_action_hash(metadata["tool_name"], normalize_action_arguments(metadata["tool_name"], target))
                if metadata.get("action_hash") != expected:
                    raise ExecutionLost("审批提案参数与检查点不一致")
            elif target != proposal["payload"]:
                raise ExecutionLost("审批提案参数与检查点不一致")
