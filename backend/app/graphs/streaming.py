"""LangGraph 流式输出适配为 SSE 事件格式，保持与前端兼容。"""

import asyncio
import json
import re
import uuid
from typing import Any, AsyncGenerator

from app.core.logging import get_logger

logger = get_logger()


_SUPERVISOR_STAGE_MESSAGES = {
    "load_task": "正在初始化组合任务...",
    "plan_task": "正在规划寻源与风险分析任务...",
    "execute_ready_tasks": "正在执行供应商专业分析...",
    "merge_evidence": "正在合并供应商证据...",
    "build_decision": "正在生成综合决策...",
    "approval_gate": "正在检查待审批操作...",
    "finalize": "正在整理最终回答...",
}


def _sse_event(event_type: str, data: dict, event_id: int | None = None) -> str:
    """Format data as SSE event string."""
    event_id_line = f"id: {event_id}\n" if event_id is not None else ""
    return f"{event_id_line}event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _workflow_status(
    status: str,
    stage: str,
    message: str,
    **details: Any,
) -> str:
    """Emit one stable, display-safe Agent lifecycle update for the workbench."""
    return _sse_event(
        "workflow_status",
        {"status": status, "stage": stage, "message": message, **details},
    )


def _render_harness_answer(answer: dict[str, Any]) -> str:
    """Render only the business-facing layer of a structured answer.

    Evidence references and limitations remain in the structured ``agent_answer``
    event for the UI's expandable data layer. They must not leak into the main
    conversational text, where opaque IDs and execution terminology distract
    from the actual business conclusion.
    """
    lines = [str(answer.get("summary") or "Agent 未形成可展示的确定性结论。")]
    for claim in answer.get("claims", []):
        if not isinstance(claim, dict):
            continue
        statement = str(claim.get("statement") or "").strip()
        if not statement:
            continue
        lines.append(f"- {statement}")
    return "\n".join(lines)


async def stream_harness_graph(
    user_message: str,
    session_id: str,
    execution_context: dict[str, Any],
    *,
    user_id: str = "",
    run_config: dict[str, Any] | None = None,
    checkpointer: Any = None,
    turn_id: str | None = None,
    run_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """Run the unified Harness and publish durable progress as it happens."""
    from app.graphs.harness import run_harness

    config = dict(run_config or {})
    configurable = dict(config.get("configurable") or {})
    active_run_id = run_id or str(uuid.uuid4())
    # A checkpoint thread is execution state, not session state. Reusing the
    # session thread lets an earlier task_specs list override this run's task.
    configurable["thread_id"] = active_run_id
    configurable.setdefault("checkpoint_ns", "chat:harness")
    config["configurable"] = configurable
    active_turn_id = turn_id or str(uuid.uuid4())
    control_context = dict(execution_context)
    control_context.pop("_control_plane", None)
    state = {
        "session_id": session_id,
        "turn_id": active_turn_id,
        "run_id": active_run_id,
        "user_id": user_id or None,
        "user_message": user_message,
        "execution_context": control_context,
        "current_task": control_context.get("current_task") or {},
    }

    queue: asyncio.Queue[tuple[str, dict[str, Any], int | None]] = asyncio.Queue()
    progress_seen = False

    async def publish(
        event_type: str,
        payload: dict[str, Any],
        *,
        durable: bool = True,
    ) -> None:
        event_id: int | None = None
        if durable and run_id and user_id:
            from app.domains.agent_run.state_store import session_state_store

            append_event = getattr(session_state_store, "append_harness_event", None)
            if callable(append_event):
                event = await asyncio.to_thread(
                    append_event,
                    active_run_id,
                    event_type,
                    payload,
                )
                event_id = int(event["event_id"])
        await queue.put((event_type, payload, event_id))

    async def progress(
        event_type: str,
        snapshot: dict[str, Any],
        already_persisted: bool,
    ) -> None:
        """Translate internal node/tool progress into public, replayable events."""
        nonlocal progress_seen
        progress_seen = True
        if event_type == "tool_call":
            await publish("tool_call", snapshot)
            return
        if event_type == "tool_result":
            await publish("tool_result", snapshot)
            return

        status = str(snapshot.get("status") or "running")
        run_details = {"run_id": active_run_id, "status": status}
        if event_type == "load_session":
            await publish("workflow_status", {
                **run_details, "stage": "understand", "message": "正在解析结构化任务上下文",
            })
        elif event_type == "resolve_turn":
            await publish("workflow_status", {
                **run_details, "stage": "understand", "message": "已完成会话与当前任务解析",
            })
        elif event_type == "build_plan":
            tasks = snapshot.get("task_specs") or []
            await publish("plan", {
                "run_id": active_run_id,
                "steps": [
                    {"tool": item.get("tool_name", ""), "args": item.get("arguments", {})}
                    for item in tasks if isinstance(item, dict)
                ],
            })
            await publish("workflow_status", {
                **run_details, "stage": "planning", "message": "已生成受预算约束的任务计划",
            })
        elif event_type == "execute_ready_tasks":
            await publish("workflow_status", {
                **run_details,
                "stage": "executing",
                "message": "正在通过统一 ToolExecutor 执行任务",
                "tool_call_count": snapshot.get("tool_call_count", 0),
                "completed_tool_count": len(snapshot.get("tool_outcomes") or []),
            })
        elif event_type == "validate_evidence":
            coverage = snapshot.get("evidence_coverage") or {}
            await publish("evidence", {
                "run_id": active_run_id,
                "records": snapshot.get("evidence_records", []),
                "coverage": coverage,
            })
            await publish("workflow_status", {
                **run_details,
                "stage": "evidence",
                "message": "正在校验证据覆盖度与 Claim 引用",
                "evidence_status": (
                    f"{len(coverage.get('covered_dimensions', []))}/"
                    f"{len(coverage.get('required_dimensions', []))} 已覆盖"
                ),
                "loop_exit_reason": snapshot.get("loop_exit_reason"),
            })
        elif event_type == "remediate":
            await publish("workflow_status", {
                **run_details,
                "stage": "executing",
                "message": "正在执行受限证据补全循环",
                "loop_exit_reason": snapshot.get("loop_exit_reason"),
            })
        elif event_type == "render_answer":
            answer = snapshot.get("answer") or {}
            await publish("agent_answer", answer)
            await publish("evidence", {
                "run_id": active_run_id,
                "records": snapshot.get("evidence_records", []),
                "coverage": snapshot.get("evidence_coverage") or {},
            })
            await publish("answer_chunk", {"text": _render_harness_answer(answer)})
        elif event_type == "persist_turn":
            await publish("workflow_status", {
                **run_details,
                "stage": "decision",
                "message": "正在整理最终回答",
                "loop_exit_reason": snapshot.get("loop_exit_reason"),
            })

    persist = None
    if run_id and user_id:
        from app.domains.agent_run.state_store import session_state_store

        async def persist(event_type: str, snapshot: dict[str, Any]) -> None:
            await asyncio.to_thread(
                session_state_store.persist_harness_snapshot,
                active_run_id,
                event_type,
                snapshot,
            )

    yield _workflow_status("running", "understand", "正在解析结构化任务上下文")
    if run_id and user_id:
        await publish("run", {"run_id": active_run_id, "turn_id": active_turn_id})
    try:
        async def run_graph() -> Any:
            from app.graphs.agent_core.narrator import narrate_answer

            return await run_harness(
                state,
                checkpointer=checkpointer,
                config=config,
                persist=persist,
                progress=progress,
                narrate=lambda answer, message: asyncio.to_thread(
                    narrate_answer, answer, message
                ),
            )

        runner = asyncio.create_task(run_graph())
        try:
            while not runner.done() or not queue.empty():
                if queue.empty():
                    try:
                        event_type, data, event_id = await asyncio.wait_for(queue.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                else:
                    event_type, data, event_id = queue.get_nowait()
                yield _sse_event(event_type, data, event_id)
            result = await runner
        finally:
            if not runner.done():
                runner.cancel()
        if not progress_seen:
            # Compatibility fallback for injected test runners and old callers.
            task_specs = result.get("task_specs", [])
            if isinstance(task_specs, list):
                await publish("plan", {"steps": [
                    {"tool": item.get("tool_name", ""), "args": item.get("arguments", {})}
                    for item in task_specs if isinstance(item, dict)
                ]}, durable=False)
            for outcome in result.get("tool_outcomes", []) if isinstance(result.get("tool_outcomes"), list) else []:
                if isinstance(outcome, dict):
                    tool_name = str(outcome.get("tool_name") or "unknown")
                    await publish("tool_call", {"tool": tool_name, "args": outcome.get("input", {})}, durable=False)
                    await publish("tool_result", {"tool": tool_name, "result": outcome}, durable=False)
            await publish("evidence", {"records": result.get("evidence_records", []), "coverage": result.get("evidence_coverage") or {}}, durable=False)
            answer = result.get("answer") or {"status": "failed", "summary": "Harness 未生成结构化回答。", "claims": [], "limitations": ["缺少 AgentAnswer"], "evidence_refs": []}
            await publish("agent_answer", answer, durable=False)
            await publish("answer_chunk", {"text": _render_harness_answer(answer)}, durable=False)
        else:
            answer = result.get("answer") or {}
        outcomes = result.get("tool_outcomes", [])
        answer_text = _render_harness_answer(answer)

        from app.graphs.agent_core.adapter import collect_supplier_references, save_execution_turn

        references: list[dict[str, Any]] = []
        for outcome in outcomes if isinstance(outcomes, list) else []:
            if isinstance(outcome, dict):
                references = collect_supplier_references(references, outcome.get("data", {}), "Harness 工具结果")
        if run_id and user_id:
            from app.domains.agent_run.state_store import session_state_store

            next_context = dict(control_context)
            next_context["history"] = [
                *list(control_context.get("history") or []),
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": answer_text},
            ]
            next_context["references"] = collect_supplier_references(
                list(control_context.get("references") or []),
                references,
                "Harness 已验证结果",
            )
            await asyncio.to_thread(
                session_state_store.update_execution_context,
                session_id,
                user_id,
                next_context,
            )
        save_execution_turn(session_id, user_message, answer_text, references)
        if references:
            await publish("references", {"items": references})
        final_status = str(answer.get("status") or "failed")
        final_stage = "completed" if final_status in {"completed", "partial"} else "decision"
        await publish("workflow_status", {
            "run_id": active_run_id,
            "status": final_status,
            "stage": final_stage,
            "message": "本轮 Harness 工作流已完成",
            "loop_exit_reason": result.get("loop_exit_reason"),
        })
        await publish("done", {"run_id": active_run_id, "status": final_status, "answer": answer_text})
        while not queue.empty():
            event_type, data, event_id = queue.get_nowait()
            yield _sse_event(event_type, data, event_id)
    except Exception as exc:
        from app.graphs import format_llm_error

        message = format_llm_error(exc)
        await publish("workflow_status", {"run_id": active_run_id, "status": "failed", "stage": "decision", "message": "Harness 工作流执行失败"})
        await publish("error", {"run_id": active_run_id, "message": message})
        while not queue.empty():
            event_type, data, event_id = queue.get_nowait()
            yield _sse_event(event_type, data, event_id)


def _event_data(event: dict[str, Any]) -> dict[str, Any]:
    """Return a mapping payload even when LangGraph emits ``data=None``."""
    data = event.get("data")
    return data if isinstance(data, dict) else {}


_ACCESS_WRITE_TOOLS = {"select_sourcing_result", "select_external_supplier_candidate"}
_ACCESS_SUCCESS_CLAIMS = re.compile(r"已(?:完成|成功)|正式成为|同意准入|准入成功")
_MAX_AGENT_TOOL_CALLS = 12


async def _checkpoint_messages(graph: Any, config: dict[str, Any]) -> list[Any]:
    """Read persisted messages when the compiled graph exposes state access."""
    get_state = getattr(graph, "aget_state", None)
    if not callable(get_state):
        return []
    try:
        snapshot = await get_state(config)
    except Exception:
        return []
    values = getattr(snapshot, "values", {})
    messages = values.get("messages", []) if isinstance(values, dict) else []
    return list(messages) if isinstance(messages, list) else []


def _has_unresolved_tool_calls(messages: list[Any]) -> bool:
    """Return whether a persisted message sequence is awaiting tool results."""
    pending: set[str] = set()
    for message in messages:
        for tool_call in getattr(message, "tool_calls", []) or []:
            call_id = tool_call.get("id") if isinstance(tool_call, dict) else None
            if call_id:
                pending.add(str(call_id))
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id:
            pending.discard(str(tool_call_id))
    return bool(pending)


def _access_write_succeeded(tool_name: str, output: Any) -> bool:
    """Only a durable application id is allowed to support an access-success claim."""
    if tool_name not in _ACCESS_WRITE_TOOLS:
        return False
    value = output
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return False
    return bool(
        isinstance(value, dict)
        and value.get("success") is True
        and value.get("application_id")
    )


def _guard_access_answer(user_message: str, answer: str, access_succeeded: bool) -> str:
    """Prevent a text-only ReAct answer from claiming a write that did not happen."""
    if "准入" not in user_message or access_succeeded:
        return answer
    if not _ACCESS_SUCCESS_CLAIMS.search(answer):
        return answer
    return "未检测到准入申请成功回执，本次准入申请尚未提交。请重新发起准入申请，并完成人工审批。"


def _supervisor_tool_name(agent: object) -> str:
    return f"{agent}_agent"


def _supervisor_interrupt_data(update: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(update, dict):
        return None
    interrupts = update.get("__interrupt__")
    if not interrupts:
        return None
    interrupt_value = interrupts[0] if isinstance(interrupts, (list, tuple)) else interrupts
    data = getattr(interrupt_value, "value", interrupt_value)
    return dict(data) if isinstance(data, dict) else {"message": str(data)}


async def stream_agent_supervisor_graph(
    graph: Any,
    user_message: str,
    session_id: str,
    run_config: dict[str, Any] | None = None,
    graph_input: Any = None,
    execution_context: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """Map Agent Supervisor updates onto the existing public SSE schema."""
    config = dict(run_config or {})
    configurable = dict(config.get("configurable") or {})
    configurable.setdefault("thread_id", session_id)
    config["configurable"] = configurable
    input_data = graph_input if graph_input is not None else {
        "run_id": session_id,
        "user_query": user_message,
        "intent": {},
    }
    full_answer = ""
    final_status = "completed"
    discovered_references: list[dict] = []

    yield _workflow_status("running", "understand", "正在分析组合寻源与风险任务...")
    yield _sse_event("thinking", {"message": "正在分析组合寻源与风险任务..."})

    try:
        async for update in graph.astream(
            input_data, config=config, stream_mode="updates"
        ):
            interrupt_data = _supervisor_interrupt_data(update)
            if interrupt_data is not None:
                from app.graphs.interrupt_store import store as store_interrupt

                store_interrupt(
                    session_id=session_id,
                    graph=graph,
                    config=config,
                    mode="agent-supervisor",
                    user_message=user_message,
                    user_id=str((execution_context or {}).get("agent_user_id") or "") or None,
                )
                yield _workflow_status("waiting_approval", "approval", "等待人工确认后继续执行")
                payload = {
                    "message": interrupt_data.get("message", "确认此操作？"),
                    "tool": interrupt_data.get("tool", "agent_supervisor"),
                    "args": interrupt_data.get("args", {}),
                    "session_id": session_id,
                    "requires_human_approval": True,
                }
                if "pending_approvals" in interrupt_data:
                    payload["pending_approvals"] = interrupt_data[
                        "pending_approvals"
                    ]
                yield _sse_event("approval_required", payload)
                # Approval is a deliberate pause. Emit a terminal marker for
                # clients whose stream reader expects every response to end
                # with `done`; the workflow itself remains resumable from the
                # checkpoint stored above.
                yield _sse_event(
                    "done",
                    {
                        "answer": payload["message"],
                        "status": "waiting_approval",
                    },
                )
                return

            for stage, output in update.items():
                if not isinstance(output, dict):
                    continue
                stage_message = _SUPERVISOR_STAGE_MESSAGES.get(stage)
                if stage_message:
                    stage_key = {
                        "load_task": "understand",
                        "plan_task": "planning",
                        "execute_ready_tasks": "executing",
                        "merge_evidence": "evidence",
                        "build_decision": "decision",
                        "approval_gate": "approval",
                        "finalize": "decision",
                    }.get(stage, "executing")
                    yield _workflow_status("running", stage_key, stage_message)
                    yield _sse_event("thinking", {"message": stage_message})

                if stage == "plan_task":
                    plan = output.get("plan", {})
                    tasks = plan.get("tasks", []) if isinstance(plan, dict) else []
                    for task in tasks:
                        if not isinstance(task, dict):
                            continue
                        yield _sse_event(
                            "tool_call",
                            {
                                "tool": _supervisor_tool_name(task.get("agent", "unknown")),
                                "args": {
                                    "task_id": task.get("task_id", ""),
                                    "depends_on": task.get("depends_on", []),
                                },
                            },
                        )

                if stage == "execute_ready_tasks":
                    results = output.get("agent_results", {})
                    if isinstance(results, dict):
                        for task_id, result in results.items():
                            if not isinstance(result, dict):
                                continue
                            agent = result.get("agent") or task_id
                            yield _sse_event(
                                "tool_result",
                                {
                                    "tool": _supervisor_tool_name(agent),
                                    "result": result,
                                },
                            )
                            from app.graphs.agent_core.adapter import collect_supplier_references

                            discovered_references = collect_supplier_references(
                                discovered_references, result, agent
                            )

                if stage == "finalize":
                    task_status = str(output.get("task_status") or "")
                    if "拒绝" in str(output.get("final_answer") or "") or task_status == "REJECTED":
                        final_status = "rejected"
                    answer = output.get("final_answer")
                    if isinstance(answer, str) and answer and answer != full_answer:
                        full_answer = answer
                        yield _sse_event("answer_chunk", {"text": answer})

        if full_answer:
            from app.graphs.agent_core.adapter import (
                collect_supplier_references,
                save_execution_turn,
            )

            discovered_references = collect_supplier_references(
                discovered_references, full_answer, "Agent 回答"
            )

            save_execution_turn(
                session_id, user_message, full_answer, discovered_references
            )
        if discovered_references:
            yield _sse_event("references", {"items": discovered_references})
        if final_status == "rejected":
            yield _workflow_status("rejected", "approval", "操作已拒绝，未写入业务数据")
        else:
            yield _workflow_status("completed", "completed", "本轮 Agent 工作流已完成")
        done_payload = {"answer": full_answer}
        if final_status == "rejected":
            done_payload["status"] = final_status
        yield _sse_event("done", done_payload)
    except Exception as exc:
        from app.graphs import format_llm_error

        yield _workflow_status("failed", "decision", "Agent 工作流执行失败")
        yield _sse_event("error", {"message": format_llm_error(exc)})


async def stream_react_graph(
    graph,
    user_message: str,
    session_id: str,
    history: list[dict] | None = None,
    run_config: dict | None = None,
    references: list[dict] | None = None,
    execution_context: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """运行 ReAct 图并 yield SSE 事件。

    Events: thinking, tool_call, tool_result, answer_chunk,
            approval_required, done, error

    当工具触发 interrupt() 时，发射 approval_required 事件并暂停。
    通过 resume 端点恢复后继续流式输出。
    """
    from app.graphs.context import build_input_messages

    from app.graphs.agent_core.adapter import build_execution_context

    resolved_context = execution_context or build_execution_context(
        session_id=session_id,
        user_message=user_message,
        history=history or [],
        references=references or [],
    )
    checkpoint_messages = await _checkpoint_messages(graph, run_config or {})
    if _has_unresolved_tool_calls(checkpoint_messages):
        yield _sse_event("error", {
            "message": "上一轮会话仍有未完成的工具执行，请等待其结束或重新打开会话后再试。"
        })
        return

    # A persisted graph already owns completed turns. Re-injecting the MongoDB
    # history appends duplicates after that state and can violate tool-call
    # ordering. Only seed durable history for a new checkpoint namespace.
    input_history = [] if checkpoint_messages else (history or [])
    input_messages = await build_input_messages(
        input_history,
        user_message,
        references,
        resolved_context,
    )

    full_answer = ""
    discovered_references: list[dict] = list(references or [])
    tool_call_count = 0
    access_succeeded = False
    buffer_access_answer = "准入" in user_message
    config = run_config or {}

    yield _workflow_status("running", "understand", "正在分析您的问题...")
    yield _sse_event("thinking", {"message": "正在分析您的问题..."})

    try:
        async for event in graph.astream_events(
            {
                "messages": input_messages,
                "conversation_state": resolved_context["conversation_state"],
                "current_task": resolved_context["current_task"],
            },
            config=config,
            version="v2",
        ):
            kind = event.get("event", "")

            # LLM token 级流式输出
            if kind == "on_chat_model_stream":
                chunk = _event_data(event).get("chunk")
                if chunk and chunk.content:
                    full_answer += chunk.content
                    if not buffer_access_answer:
                        yield _sse_event("answer_chunk", {"text": chunk.content})

            # 工具调用开始
            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                tool_input = _event_data(event).get("input", {})
                yield _sse_event("tool_call", {"tool": tool_name, "args": tool_input})
                yield _workflow_status("running", "executing", f"正在执行工具：{tool_name}")
                tool_call_count += 1
                if tool_call_count > _MAX_AGENT_TOOL_CALLS:
                    raise RuntimeError(
                        f"Agent 工具调用超过 {_MAX_AGENT_TOOL_CALLS} 次，已停止重复执行；请缩小本次任务范围"
                    )

            # 工具调用结束
            elif kind == "on_tool_end":
                tool_name = event.get("name", "")
                output = _event_data(event).get("output", "")
                from app.graphs.agent_core.adapter import collect_supplier_references

                discovered_references = collect_supplier_references(
                    discovered_references, output, tool_name
                )
                if isinstance(output, str):
                    result = output
                else:
                    result = json.dumps(output, ensure_ascii=False, default=str)
                access_succeeded = access_succeeded or _access_write_succeeded(tool_name, output)
                if len(result) > 2000:
                    result = result[:2000] + "...(截断)"
                yield _sse_event("tool_result", {"tool": tool_name, "result": result})
                yield _workflow_status("running", "evidence", f"已收到工具结果：{tool_name}")

                # 自动注入图表事件
                from app.graphs.chart_data import _try_auto_chart
                chart = _try_auto_chart(tool_name, result)
                if chart:
                    yield _sse_event("chart_data", chart)

            # With a checkpointer, LangGraph reports interrupt() from a tool
            # as a tool error event and then closes the graph normally.  It is
            # not propagated to the outer try/except, so convert it here.
            elif kind == "on_tool_error":
                error = _event_data(event).get("error")
                if not isinstance(error, Exception) or not _is_graph_interrupt(error):
                    continue
                interrupt_data = _extract_interrupt_data(error)
                from app.graphs.interrupt_store import store as store_interrupt

                store_interrupt(
                    session_id=session_id,
                    graph=graph,
                    config=config,
                    mode="react",
                    user_message=user_message,
                    user_id=str((execution_context or {}).get("agent_user_id") or "") or None,
                )
                yield _workflow_status("waiting_approval", "approval", "等待人工确认后继续执行")
                yield _sse_event("approval_required", {
                    "message": interrupt_data.get("message", "确认此操作？"),
                    "tool": interrupt_data.get("tool", event.get("name", "")),
                    "args": interrupt_data.get("args", {}),
                    "session_id": session_id,
                    "requires_human_approval": True,
                })
                return

            # LLM 完成（非流式响应或工具调用后的回答）
            elif kind == "on_chat_model_end":
                output = _event_data(event).get("output")
                content = output.content if output and hasattr(output, "content") else ""
                if content:
                    full_answer = content
                    if not buffer_access_answer:
                        yield _sse_event("answer_chunk", {"text": content})

            # Some guardrail nodes return an AIMessage directly instead of
            # invoking the LLM (for example, the current admission boundary).
            # Such messages do not produce ``on_chat_model_end`` and must still
            # become the public answer. Normal LLM turns already populated
            # ``full_answer`` above, so only use this fallback when needed.
            elif kind == "on_chain_end" and event.get("name") == "agent" and not full_answer:
                output = _event_data(event).get("output")
                messages = output.get("messages", []) if isinstance(output, dict) else []
                message = messages[-1] if messages else None
                content = getattr(message, "content", "") if message else ""
                if isinstance(content, str) and content:
                    full_answer = content
                    if not buffer_access_answer:
                        yield _sse_event("answer_chunk", {"text": content})

            # Reflector 发现问题时通知
            elif kind == "on_chain_end" and event.get("name") == "reflector":
                output = _event_data(event).get("output", {})
                if isinstance(output, dict):
                    feedback = output.get("reflection_feedback", "")
                    if feedback:
                        yield _sse_event("thinking", {"message": f"正在优化回答: {feedback}"})

        full_answer = _guard_access_answer(user_message, full_answer, access_succeeded)
        if buffer_access_answer and full_answer:
            yield _sse_event("answer_chunk", {"text": full_answer})

        # 保存对话历史
        if full_answer:
            from app.graphs.agent_core.adapter import (
                collect_supplier_references,
                save_execution_turn,
            )

            discovered_references = collect_supplier_references(
                discovered_references, full_answer, "Agent 回答"
            )

            save_execution_turn(
                session_id, user_message, full_answer, discovered_references
            )

        if discovered_references:
            yield _sse_event("references", {"items": discovered_references})

        yield _workflow_status("completed", "completed", "本轮 Agent 工作流已完成")
        yield _sse_event("done", {"answer": full_answer})

    except Exception as e:
        # 检查是否为 LangGraph 中断（Human-in-the-Loop）
        if _is_graph_interrupt(e):
            interrupt_data = _extract_interrupt_data(e)

            # 存储暂停的图状态，供 resume 端点恢复
            from app.graphs.interrupt_store import store as store_interrupt

            store_interrupt(
                session_id=session_id,
                graph=graph,
                config=config,
                mode="react",
                user_message=user_message,
                user_id=str((execution_context or {}).get("agent_user_id") or "") or None,
            )

            yield _workflow_status("waiting_approval", "approval", "等待人工确认后继续执行")
            yield _sse_event("approval_required", {
                "message": interrupt_data.get("message", "确认此操作？"),
                "tool": interrupt_data.get("tool", ""),
                "args": interrupt_data.get("args", {}),
                "session_id": session_id,
            })
            return

        logger.exception(
            "react_graph_stream_failed",
            session_id=session_id,
            error=str(e),
        )
        from app.graphs import format_llm_error

        yield _workflow_status("failed", "decision", "Agent 工作流执行失败")
        yield _sse_event("error", {"message": format_llm_error(e)})


def _is_graph_interrupt(exc: Exception) -> bool:
    """判断异常是否为 LangGraph 中断。"""
    exc_name = type(exc).__name__
    return exc_name in ("GraphInterrupt", "NodeInterrupt", "Interrupt")


def _extract_interrupt_data(exc: Exception) -> dict:
    """从 GraphInterrupt 异常中提取中断数据。"""
    def unwrap(value: Any) -> dict | None:
        if isinstance(value, dict):
            return value
        if isinstance(value, (list, tuple)) and value:
            return unwrap(value[0])
        nested_value = getattr(value, "value", None)
        if nested_value is not None:
            return unwrap(nested_value)
        return None

    try:
        # GraphInterrupt 通常有 args[0] 作为中断值
        if hasattr(exc, "args") and exc.args:
            data = unwrap(exc.args[0])
            if data is not None:
                return data
            return {"message": str(exc.args[0])}
    except Exception:
        pass

    return {"message": str(exc)}
