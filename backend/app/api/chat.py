import asyncio
import json
import uuid
from typing import Any

from pydantic import BaseModel

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.logging import get_logger

router = APIRouter(dependencies=[Depends(get_current_user)])
logger = get_logger()


async def _langgraph_react_stream(
    session_id: str,
    message: str,
    preference_context: str = "",
    execution_context: dict[str, Any] | None = None,
):
    """LangGraph ReAct 模式流式输出。"""
    from app.graphs.react_graph import build_react_graph
    from app.graphs.streaming import stream_react_graph
    from app.graphs.agent_core.adapter import load_execution_context
    from app.graphs.sourcing_risk_v2.checkpointer import get_sourcing_risk_checkpointer

    graph = build_react_graph(
        preference_context,
        checkpointer=await get_sourcing_risk_checkpointer(),
    )
    context = execution_context or load_execution_context(session_id, message)
    # 传入 config 用于 Human-in-the-Loop 恢复
    from app.graphs.chat_checkpoint import chat_checkpoint_config

    run_config = chat_checkpoint_config(session_id, "react")
    async for event in stream_react_graph(
        graph,
        message,
        session_id,
        context["history"],
        run_config,
        context["references"],
        context,
    ):
        yield event


async def _langgraph_plan_execute_stream(
    session_id: str,
    message: str,
    preference_context: str = "",
    execution_context: dict[str, Any] | None = None,
):
    """LangGraph Plan-Execute 模式流式输出。"""
    from app.graphs.plan_execute_graph import stream_plan_execute_graph
    from app.graphs.agent_core.adapter import load_execution_context

    context = execution_context or load_execution_context(session_id, message)
    async for event in stream_plan_execute_graph(
        message,
        session_id,
        context["history"],
        preference_context,
        context["references"],
        context,
    ):
        yield event


async def _langgraph_supervisor_stream(
    session_id: str,
    message: str,
    preference_context: str = "",
    execution_context: dict[str, Any] | None = None,
):
    """LangGraph Supervisor 多智能体模式流式输出。"""
    from app.graphs.supervisor_graph import stream_supervisor_graph
    from app.graphs.agent_core.adapter import load_execution_context

    context = execution_context or load_execution_context(session_id, message)
    async for event in stream_supervisor_graph(
        message,
        session_id,
        context["history"],
        preference_context,
        context["references"],
        context,
    ):
        yield event


async def _langgraph_sourcing_stream(
    session_id: str,
    message: str,
    preference_context: str = "",
    execution_context: dict[str, Any] | None = None,
):
    """LangGraph Sourcing 寻源子图流式输出。"""
    from app.graphs.agents.sourcing import stream_sourcing_graph

    async for event in stream_sourcing_graph(
        session_id, message, preference_context, execution_context
    ):
        yield event


async def _langgraph_agent_supervisor_stream(
    session_id: str,
    message: str,
    preference_context: str = "",
    execution_context: dict[str, Any] | None = None,
):
    """Agent Supervisor 组合寻源与风险任务流式输出。"""
    del preference_context

    from app.graphs.agent_supervisor.graph import build_agent_supervisor_graph
    from app.graphs.streaming import stream_agent_supervisor_graph
    from app.graphs.agent_core.adapter import (
        build_agent_supervisor_graph_input,
        load_execution_context,
        validate_execution_context,
    )

    graph = build_agent_supervisor_graph()
    from app.graphs.chat_checkpoint import chat_checkpoint_config

    run_config = chat_checkpoint_config(session_id, "agent-supervisor")
    try:
        context = execution_context or load_execution_context(session_id, message)
    except Exception:
        # Conversation references are an enhancement; an unavailable MongoDB
        # must not alter the Supervisor's existing read-only execution path.
        context = {
            "history": [],
            "references": [],
            "conversation_state": {},
            "current_task": {},
        }
    context = validate_execution_context(
        context, source="langgraph_agent_supervisor_stream"
    )
    agent_user_id = context.get("agent_user_id")
    if not isinstance(agent_user_id, str) or not agent_user_id:
        # Anonymous/read-only chat still has to receive the context resolved by
        # the shared adapter.  Otherwise current-turn LLM extraction is silently
        # discarded for this branch and the graph falls back to stale references.
        stream = stream_agent_supervisor_graph(
            graph,
            message,
            session_id,
            run_config,
            graph_input=build_agent_supervisor_graph_input(
                context, run_id=session_id, user_message=message
            ),
            execution_context=context,
        )
    else:
        from app.domains.agent_run.schemas import CreateSourcingRiskRunRequest
        from app.domains.agent_run.service import create_sourcing_risk_run

        run = await asyncio.to_thread(
            create_sourcing_risk_run,
            CreateSourcingRiskRunRequest(requirement_text=message),
            agent_user_id,
            "analyst",
        )
        run_id = str(run["id"])
        stream = stream_agent_supervisor_graph(
            graph,
            message,
            session_id,
            run_config,
            graph_input=build_agent_supervisor_graph_input(
                context, run_id=run_id, user_message=message
            ),
            execution_context=context,
        )
    async for event in stream:
        yield event


async def _langgraph_harness_stream(
    session_id: str,
    message: str,
    preference_context: str = "",
    execution_context: dict[str, Any] | None = None,
):
    """Unified Harness Runtime stream; legacy graphs remain explicit fallbacks."""
    del preference_context

    from app.graphs.agent_core.adapter import load_execution_context
    from app.graphs.sourcing_risk_v2.checkpointer import get_sourcing_risk_checkpointer
    from app.graphs.chat_checkpoint import chat_checkpoint_config
    from app.graphs.streaming import stream_harness_graph

    context = execution_context or load_execution_context(session_id, message)
    async for event in stream_harness_graph(
        message,
        session_id,
        context,
        user_id=str(context.get("agent_user_id") or ""),
        run_config=chat_checkpoint_config(session_id, "harness"),
        checkpointer=await get_sourcing_risk_checkpointer(),
    ):
        yield event


async def _langgraph_parallel_stream(
    session_id: str,
    message: str,
    preference_context: str = "",
    execution_context: dict[str, Any] | None = None,
):
    """LangGraph Parallel 并行多 Agent Map-Reduce 流式输出。"""
    from app.graphs.parallel_graph import stream_parallel_graph
    from app.graphs.agent_core.adapter import load_execution_context

    context = execution_context or load_execution_context(session_id, message)
    async for event in stream_parallel_graph(
        message,
        session_id,
        context["history"],
        preference_context,
        context["references"],
        context,
    ):
        yield event


async def _langgraph_react_reflection_stream(
    session_id: str,
    message: str,
    preference_context: str = "",
    execution_context: dict[str, Any] | None = None,
):
    """LangGraph ReAct + Self-Reflection 流式输出。"""
    from app.graphs.react_graph import build_react_graph_with_reflection
    from app.graphs.streaming import stream_react_graph
    from app.graphs.agent_core.adapter import load_execution_context
    from app.graphs.sourcing_risk_v2.checkpointer import get_sourcing_risk_checkpointer

    graph = build_react_graph_with_reflection(
        preference_context,
        checkpointer=await get_sourcing_risk_checkpointer(),
    )
    context = execution_context or load_execution_context(session_id, message)
    from app.graphs.chat_checkpoint import chat_checkpoint_config

    run_config = chat_checkpoint_config(session_id, "react-reflection")
    async for event in stream_react_graph(
        graph,
        message,
        session_id,
        context["history"],
        run_config,
        context["references"],
        context,
    ):
        yield event


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    mode: str = "auto"  # auto/harness use Harness; old modes require the development compatibility switch


class ResumeRequest(BaseModel):
    session_id: str
    approved: bool = True


async def _rebuild_paused_graph(paused: dict[str, Any]):
    """Recreate a graph from durable resume metadata after a process restart."""
    mode = str(paused.get("mode") or "")
    from app.graphs.sourcing_risk_v2.checkpointer import get_sourcing_risk_checkpointer

    if mode == "agent-supervisor":
        from app.graphs.agent_supervisor.graph import build_agent_supervisor_graph

        return build_agent_supervisor_graph(await get_sourcing_risk_checkpointer())
    if mode == "react":
        from app.graphs.react_graph import build_react_graph

        return build_react_graph(checkpointer=await get_sourcing_risk_checkpointer())
    if mode == "react-reflection":
        from app.graphs.react_graph import build_react_graph_with_reflection

        return build_react_graph_with_reflection(
            checkpointer=await get_sourcing_risk_checkpointer()
        )
    if mode == "sourcing":
        from app.graphs.agents.sourcing import build_sourcing_graph

        return build_sourcing_graph(await get_sourcing_risk_checkpointer())
    if mode == "plan-execute":
        from app.graphs.plan_execute_graph import build_plan_execute_graph

        return build_plan_execute_graph()
    if mode == "supervisor":
        from app.graphs.supervisor_graph import build_supervisor_graph

        return build_supervisor_graph()
    if mode == "parallel":
        from app.graphs.parallel_graph import build_parallel_graph

        return build_parallel_graph(enable_reflection=True)
    raise ValueError(f"无法重建审批恢复图: {mode or 'unknown'}")


_CHAT_MODE_ALIASES = {
    "harness": "langgraph-harness",
    "react": "langgraph-react",
    "plan-execute": "langgraph-plan-execute",
    "multi-agent": "langgraph-multi-agent",
    "sourcing": "langgraph-sourcing",
    "parallel": "langgraph-parallel",
    "react-reflection": "langgraph-react-reflection",
    "agent-supervisor": "langgraph-agent-supervisor",
}
_LEGACY_CHAT_MODES = {
    "langgraph-react",
    "langgraph-plan-execute",
    "langgraph-multi-agent",
    "langgraph-sourcing",
    "langgraph-parallel",
    "langgraph-react-reflection",
}


def _legacy_chat_compat_enabled() -> bool:
    """Allow old graph comparison only in an explicitly enabled dev process."""
    return settings.DEBUG and settings.AGENT_CHAT_LEGACY_COMPAT_ENABLED


def _select_chat_stream(mode: str, *, requested_action: str) -> Any:
    """Resolve one chat stream entrypoint with Harness as the safe default."""
    normalized_mode = _CHAT_MODE_ALIASES.get(mode, mode)
    if normalized_mode in {"", "auto", "langgraph-harness"}:
        if requested_action != "none":
            return _langgraph_agent_supervisor_stream
        return _langgraph_harness_stream
    if normalized_mode == "langgraph-agent-supervisor":
        return _langgraph_agent_supervisor_stream
    legacy_streams = {
        "langgraph-react": _langgraph_react_stream,
        "langgraph-plan-execute": _langgraph_plan_execute_stream,
        "langgraph-multi-agent": _langgraph_supervisor_stream,
        "langgraph-sourcing": _langgraph_sourcing_stream,
        "langgraph-parallel": _langgraph_parallel_stream,
        "langgraph-react-reflection": _langgraph_react_reflection_stream,
    }
    if normalized_mode in _LEGACY_CHAT_MODES and _legacy_chat_compat_enabled():
        return legacy_streams[normalized_mode]
    if normalized_mode in _LEGACY_CHAT_MODES:
        logger.info(
            "chat_legacy_mode_normalized_to_harness",
            requested_mode=mode,
            compatibility_enabled=False,
        )
    return _langgraph_harness_stream


def _optional_agent_user_id(request: Request) -> str:
    """Return the signed access-token subject without making read-only chat private."""
    from app.core.security import decode_token

    cookies = getattr(request, "cookies", {})
    headers = getattr(request, "headers", {})
    token = cookies.get("access_token")
    if not token:
        token = headers.get("Authorization", "").removeprefix("Bearer ")
    payload = decode_token(token) if token else None
    if not isinstance(payload, dict) or payload.get("type") != "access":
        return ""
    user_id = payload.get("sub")
    return user_id if isinstance(user_id, str) else ""


@router.post(
    "/stream",
    summary="AI 智能对话（流式 SSE）",
    description="以 Server-Sent Events 流式返回 AI 智能体的对话响应。支持多种执行模式。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def chat_stream_endpoint(req: ChatRequest, request: Request):
    """Streaming chat endpoint using SSE (Server-Sent Events)."""
    sid = req.session_id or str(uuid.uuid4())
    user_id = getattr(request.state, "user_id", "") or _optional_agent_user_id(request)
    from app.domains.auth.preferences import build_preference_context
    pref_ctx = build_preference_context(user_id) if user_id else ""

    async def event_generator():
        # Send session_id first (before any blocking routing/classification)
        yield f"event: session\ndata: {sid}\n\n"

        from app.services.agent_session_guard import (
            acquire_agent_session_run,
            release_agent_session_run,
            renew_agent_session_run,
        )

        try:
            run_token = await asyncio.to_thread(acquire_agent_session_run, sid)
        except RuntimeError:
            yield f"event: error\ndata: {json.dumps({'message': '会话执行保护暂不可用，请稍后重试。'}, ensure_ascii=False)}\n\n"
            return
        if run_token is None:
            yield f"event: error\ndata: {json.dumps({'message': '该会话上一轮仍在处理中，请等待完成后再发送。'}, ensure_ascii=False)}\n\n"
            return

        try:
            # Resolve conversation targets before rule preflight or intent routing.
            # This makes ConversationState the one authority for company references.
            execution_context = {
                "history": [],
                "references": [],
                "conversation_state": {},
                "current_task": {},
            }
            try:
                from app.graphs.agent_core.adapter import (
                    ExecutionContextContractViolation,
                    load_execution_context,
                    validate_execution_context,
                )

                execution_context = load_execution_context(sid, req.message)
                validate_execution_context(
                    execution_context, source="chat_stream_endpoint"
                )
            except ExecutionContextContractViolation as exc:
                yield f"event: error\ndata: {json.dumps({'message': '会话上下文校验失败，已停止执行。请重试或联系开发人员。'}, ensure_ascii=False)}\n\n"
                logger.error("chat_context_contract_rejected", error=str(exc))
                return
            except Exception as exc:
                logger.warning("chat_context_load_failed", error=str(exc))
                yield f"event: error\ndata: {json.dumps({'message': '会话上下文暂不可用，已停止执行，请稍后重试。'}, ensure_ascii=False)}\n\n"
                return
            execution_context["agent_user_id"] = user_id

            # Programmatic clarification is only a fallback after structured state.
            from app.services.clarification import detect_clarification_needed
            supplier_references = list(execution_context.get("references") or [])
            conversation_state = dict(execution_context.get("conversation_state") or {})
            current_task = dict(execution_context.get("current_task") or {})
            resolved_target_names = list(
                current_task.get("target_supplier_names")
                or conversation_state.get("selected_supplier_names")
                or []
            )
            has_structured_context = bool(
                supplier_references or conversation_state.get("active_suppliers")
            )
            clar = detect_clarification_needed(
                req.message,
                supplier_references=supplier_references,
                resolved_target_names=resolved_target_names,
                has_structured_context=has_structured_context,
            )
            if clar:
                yield f"event: clarification\ndata: {json.dumps({'message': clar.message, 'missing': clar.missing, 'missing_fields': clar.missing}, ensure_ascii=False)}\n\n"
                return

            # Resolve mode only after target resolution and fallback clarification.
            mode = req.mode
            if mode == "auto":
                mode = "langgraph-harness"
            requested_action = str(
                (execution_context.get("llm_intent") or {}).get("requested_action") or "none"
            )
            stream_fn = _select_chat_stream(mode, requested_action=requested_action)

            # Stream the chat response
            async for event in stream_fn(
                sid,
                req.message,
                pref_ctx,
                execution_context=execution_context,
            ):
                await asyncio.to_thread(renew_agent_session_run, sid, run_token)
                yield event
        finally:
            await asyncio.to_thread(release_agent_session_run, sid, run_token)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/resume",
    summary="恢复暂停的对话（审批确认）",
    description="当 Agent 需要用户确认高风险操作时，此端点恢复暂停的图执行。",
    responses={
        404: {"description": "无暂停的会话"},
    },
)
async def resume_endpoint(req: ResumeRequest):
    """Resume a paused graph after user approval/denial."""
    from app.domains.agent_run.chat_interrupt_repo import take_chat_interrupt

    paused = take_chat_interrupt(req.session_id)
    if not paused:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="无暂停的会话，可能已过期")

    graph = paused.get("graph")
    if graph is None:
        try:
            graph = await _rebuild_paused_graph(paused)
        except Exception as exc:
            from fastapi import HTTPException

            raise HTTPException(status_code=503, detail="审批恢复图不可用，请重新发起任务") from exc
    config = paused.get("config") or {"configurable": {"thread_id": req.session_id}}
    mode = str(paused.get("mode") or "")
    user_message = str(paused.get("user_message") or "")

    async def resume_generator():
        from langgraph.types import Command
        resume_value = {"approved": req.approved}
        cmd = Command(resume=resume_value)

        try:
            if mode == "agent-supervisor":
                from app.graphs.streaming import stream_agent_supervisor_graph

                async for event in stream_agent_supervisor_graph(
                    graph,
                    user_message,
                    req.session_id,
                    config,
                    graph_input=cmd,
                ):
                    yield event
                return

            from app.graphs.streaming import _workflow_status
            yield _workflow_status("running", "executing", "正在恢复并执行已确认的操作")
            async for event in graph.astream_events(cmd, config=config, version="v2"):
                kind = event.get("event", "")

                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and chunk.content:
                        yield f"event: answer_chunk\ndata: {json.dumps({'text': chunk.content}, ensure_ascii=False)}\n\n"

                elif kind == "on_tool_start":
                    tool_name = event.get("name", "")
                    tool_input = event.get("data", {}).get("input", {})
                    yield f"event: tool_call\ndata: {json.dumps({'tool': tool_name, 'args': tool_input}, ensure_ascii=False)}\n\n"

                elif kind == "on_tool_end":
                    tool_name = event.get("name", "")
                    output = event.get("data", {}).get("output", "")
                    if not isinstance(output, str):
                        output = json.dumps(output, ensure_ascii=False, default=str)
                    if len(output) > 2000:
                        output = output[:2000] + "...(截断)"
                    yield f"event: tool_result\ndata: {json.dumps({'tool': tool_name, 'result': output}, ensure_ascii=False)}\n\n"

                elif kind == "on_chat_model_end":
                    output = event.get("data", {}).get("output")
                    content = output.content if output and hasattr(output, "content") else ""
                    if content:
                        yield f"event: answer_chunk\ndata: {json.dumps({'text': content}, ensure_ascii=False)}\n\n"

            yield _workflow_status("completed", "completed", "本轮 Agent 工作流已完成")
            yield f"event: done\ndata: {json.dumps({'answer': ''}, ensure_ascii=False)}\n\n"

        except Exception as e:
            from app.graphs import format_llm_error
            msg = format_llm_error(e)
            yield _workflow_status("failed", "decision", "恢复执行失败")
            yield f"event: error\ndata: {json.dumps({'message': msg}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        resume_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
