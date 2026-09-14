import asyncio
import json
import uuid
from collections.abc import Iterator
from typing import Any
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_user
from app.core.logging import get_logger

router = APIRouter(dependencies=[Depends(get_current_user)])
logger = get_logger()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _normalize_session_id(session_id: str, user_id: str) -> str:
    """Keep legacy browser IDs stable while satisfying PostgreSQL UUID columns."""
    if not user_id or not _is_uuid(user_id) or _is_uuid(session_id):
        return session_id
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"supplisense:chat-session:{user_id}:{session_id}"))


def _ensure_agent_session(session_id: str, user_id: str) -> None:
    """Ensure the Supervisor compatibility path has a durable session parent."""
    from app.domains.agent_run.state_store import session_state_store

    session = session_state_store.get_session(session_id)
    if session is None:
        session_state_store.create_session(user_id, session_id=session_id)
        return
    if str(session.user_id or "") != user_id:
        raise PermissionError("Agent 会话不属于当前用户")


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
        # must not turn the write request into an unbound action.
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

        if _is_uuid(session_id) and _is_uuid(agent_user_id):
            await asyncio.to_thread(_ensure_agent_session, session_id, agent_user_id)

        try:
            run = await asyncio.to_thread(
                create_sourcing_risk_run,
                CreateSourcingRiskRunRequest(requirement_text=message),
                agent_user_id,
                "purchaser",
                session_id,
            )
        except TypeError as exc:
            # Keep older injected compatibility callables working while the
            # production lifecycle persists the chat session on the Run.
            if "positional argument" not in str(exc) and "session_id" not in str(exc):
                raise
            run = await asyncio.to_thread(
                create_sourcing_risk_run,
                CreateSourcingRiskRunRequest(requirement_text=message),
                agent_user_id,
                "purchaser",
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
    """Unified Harness Runtime stream for all read-only chat requests."""
    del preference_context

    from app.graphs.agent_core.adapter import load_execution_context
    from app.graphs.sourcing_risk_v2.checkpointer import get_sourcing_risk_checkpointer
    from app.graphs.chat_checkpoint import chat_checkpoint_config
    from app.graphs.streaming import stream_harness_graph

    context = execution_context or load_execution_context(session_id, message)
    control_plane = context.get("_control_plane") if isinstance(context, dict) else {}
    control_plane = control_plane if isinstance(control_plane, dict) else {}
    async for event in stream_harness_graph(
        message,
        session_id,
        context,
        user_id=str(context.get("agent_user_id") or ""),
        run_config=chat_checkpoint_config(session_id, "harness"),
        checkpointer=await get_sourcing_risk_checkpointer(),
        turn_id=str(control_plane.get("turn_id") or "") or None,
        run_id=str(control_plane.get("run_id") or "") or None,
    ):
        yield event


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    mode: str = "auto"  # auto/harness use Harness; agent-supervisor is the write-approval compatibility entry


@router.get("/runs/{run_id}/events", summary="恢复聊天 Harness 事件")
async def chat_run_events(
    run_id: UUID,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
    current_user: Any = Depends(get_current_user),
):
    """Replay a persisted chat run from a cursor after a browser reconnect."""
    from app.domains.agent_run.service import stream_harness_events

    user_id = str(getattr(current_user, "id", ""))
    user_role = str(getattr(getattr(current_user, "role", None), "value", ""))

    def event_generator() -> Iterator[str]:
        for event in stream_harness_events(
            str(run_id), last_event_id or 0, user_id, user_role
        ):
            if event["event_type"] == "keepalive":
                yield ": keepalive\n\n"
            else:
                yield (
                    f"id: {event['event_id']}\n"
                    f"event: {event['event_type']}\n"
                    f"data: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
                )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    raise ValueError(f"无法重建审批恢复图: {mode or 'unknown'}")


_CHAT_MODE_ALIASES = {
    "harness": "langgraph-harness",
    "agent-supervisor": "langgraph-agent-supervisor",
}


def _select_chat_stream(mode: str, *, requested_action: str) -> Any:
    """Resolve chat to Harness, retaining only the durable write boundary."""
    normalized_mode = _CHAT_MODE_ALIASES.get(mode, mode)
    if requested_action != "none" or normalized_mode == "langgraph-agent-supervisor":
        return _langgraph_agent_supervisor_stream
    if normalized_mode not in {"", "auto", "langgraph-harness"}:
        logger.info("chat_legacy_mode_removed", requested_mode=mode)
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
    description="以 Server-Sent Events 流式返回统一 Harness 的只读分析响应；写操作进入人工审批流程。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def chat_stream_endpoint(req: ChatRequest, request: Request):
    """Streaming chat endpoint using SSE (Server-Sent Events)."""
    sid = req.session_id or str(uuid.uuid4())
    user_id = getattr(request.state, "user_id", "") or _optional_agent_user_id(request)
    user_role = ""
    if user_id:
        try:
            from app.domains.auth.service import get_user_by_id

            current_user = get_user_by_id(user_id)
            user_role = str(getattr(getattr(current_user, "role", None), "value", ""))
        except Exception as exc:
            logger.warning("chat_user_role_load_failed", user_id=user_id, error=str(exc))
    sid = _normalize_session_id(sid, user_id)
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

                if _is_uuid(sid) and _is_uuid(user_id):
                    execution_context = load_execution_context(sid, req.message, user_id)
                else:
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
            from app.services.clarification import (
                external_assessment_clarification,
                formal_supplier_identity_clarification,
                review_scope_clarification,
            )
            identity_clarification = await asyncio.to_thread(
                formal_supplier_identity_clarification,
                resolved_target_names,
                req.message,
            )
            if identity_clarification:
                yield f"event: clarification\ndata: {json.dumps({'message': identity_clarification.message, 'missing': identity_clarification.missing, 'missing_fields': identity_clarification.missing, 'candidates': identity_clarification.candidates, 'status': 'stopped', 'stage': 'understand'}, ensure_ascii=False)}\n\n"
                return
            # Scope enforcement is meaningful only for a verified application
            # account.  The protected route rejects invalid JWT subjects; the
            # transient identifier branch is retained for legacy/test clients
            # and must not be sent to PostgreSQL or produce a false stop.
            scope_clarification = (
                await asyncio.to_thread(
                    review_scope_clarification,
                    resolved_target_names,
                    supplier_references,
                    user_id,
                    user_role,
                    req.message,
                )
                if _is_uuid(user_id)
                else None
            )
            if scope_clarification:
                yield f"event: clarification\ndata: {json.dumps({'message': scope_clarification.message, 'missing': scope_clarification.missing, 'missing_fields': scope_clarification.missing, 'status': 'stopped', 'stage': 'understand'}, ensure_ascii=False)}\n\n"
                return
            external_clarification = await asyncio.to_thread(
                external_assessment_clarification,
                resolved_target_names,
                supplier_references,
                req.message,
            )
            if external_clarification:
                yield f"event: clarification\ndata: {json.dumps({'message': external_clarification.message, 'missing': external_clarification.missing, 'missing_fields': external_clarification.missing, 'status': 'stopped', 'stage': 'understand'}, ensure_ascii=False)}\n\n"
                return
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
                yield f"event: clarification\ndata: {json.dumps({'message': clar.message, 'missing': clar.missing, 'missing_fields': clar.missing, 'status': 'stopped', 'stage': 'understand'}, ensure_ascii=False)}\n\n"
                return

            # Resolve mode only after target resolution and fallback clarification.
            mode = req.mode
            requested_action = str(
                (execution_context.get("llm_intent") or {}).get("requested_action") or "none"
            )
            stream_fn = _select_chat_stream(mode, requested_action=requested_action)

            if (
                stream_fn is _langgraph_harness_stream
                and _is_uuid(sid)
                and _is_uuid(user_id)
            ):
                from app.domains.agent_run.state_store import session_state_store

                control = await asyncio.to_thread(
                    session_state_store.start_harness_turn,
                    sid,
                    user_id,
                    req.message,
                    request={"mode": req.mode},
                    state={"execution_context": execution_context},
                )
                execution_context["_control_plane"] = {
                    "turn_id": str(control["turn"].id),
                    "run_id": str(control["run"].id),
                }

            # Stream the chat response
            try:
                async for event in stream_fn(
                    sid,
                    req.message,
                    pref_ctx,
                    execution_context=execution_context,
                ):
                    await asyncio.to_thread(renew_agent_session_run, sid, run_token)
                    yield event
            except Exception as exc:
                logger.exception("chat_stream_failed", error=str(exc), session_id=sid)
                yield f"event: error\ndata: {json.dumps({'message': 'Agent 工作流执行失败，请重试。'}, ensure_ascii=False)}\n\n"
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
async def resume_endpoint(req: ResumeRequest, current_user: Any = Depends(get_current_user)):
    """Resume a paused graph after user approval/denial."""
    from app.domains.agent_run.chat_interrupt_repo import (
        ack_chat_interrupt,
        claim_chat_interrupt,
        release_chat_interrupt,
        take_chat_interrupt,
    )

    authenticated_user_id = getattr(current_user, "id", None)
    claim_token: str | None = None
    if isinstance(authenticated_user_id, str) and authenticated_user_id:
        paused = claim_chat_interrupt(req.session_id, authenticated_user_id)
        claim_token = str(paused.get("claim_token")) if paused and paused.get("claim_token") else None
    else:
        # Direct unit callers from the legacy compatibility surface do not
        # receive FastAPI's dependency injection object. Production HTTP
        # requests always take the user-bound claim path above.
        paused = take_chat_interrupt(req.session_id)
    if not paused:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="无暂停的会话，可能已过期")

    graph = paused.get("graph")
    if graph is None:
        try:
            graph = await _rebuild_paused_graph(paused)
        except Exception as exc:
            if claim_token:
                release_chat_interrupt(req.session_id, claim_token)
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
                if claim_token:
                    ack_chat_interrupt(req.session_id, claim_token)
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
            if claim_token:
                ack_chat_interrupt(req.session_id, claim_token)

        except Exception as e:
            if claim_token:
                release_chat_interrupt(req.session_id, claim_token)
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
