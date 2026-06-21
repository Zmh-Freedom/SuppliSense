import asyncio
import uuid

from pydantic import BaseModel

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_user
from app.services.agent import chat as agent_chat

router = APIRouter(dependencies=[Depends(get_current_user)])


async def _langgraph_react_stream(session_id: str, message: str, preference_context: str = ""):
    """LangGraph ReAct 模式流式输出。"""
    from app.graphs.react_graph import build_react_graph
    from app.graphs.streaming import stream_react_graph
    from app.services.agent import _load_history

    graph = build_react_graph(preference_context)
    history = _load_history(session_id)
    async for event in stream_react_graph(graph, message, session_id, history):
        yield event


async def _langgraph_plan_execute_stream(session_id: str, message: str, preference_context: str = ""):
    """LangGraph Plan-Execute 模式流式输出。"""
    from app.graphs.plan_execute_graph import stream_plan_execute_graph
    from app.services.agent import _load_history

    history = _load_history(session_id)
    async for event in stream_plan_execute_graph(message, session_id, history, preference_context):
        yield event


async def _langgraph_supervisor_stream(session_id: str, message: str, preference_context: str = ""):
    """LangGraph Supervisor 多智能体模式流式输出。"""
    from app.graphs.supervisor_graph import stream_supervisor_graph
    from app.services.agent import _load_history

    history = _load_history(session_id)
    async for event in stream_supervisor_graph(message, session_id, history, preference_context):
        yield event


async def _langgraph_sourcing_stream(session_id: str, message: str, preference_context: str = ""):
    """LangGraph Sourcing 寻源子图流式输出。"""
    from app.graphs.agents.sourcing import stream_sourcing_graph

    async for event in stream_sourcing_graph(session_id, message, preference_context):
        yield event


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    mode: str = "auto"  # "auto" | "react" | "plan-execute" | "multi-agent" | "langgraph-react" | "langgraph-plan-execute" | "langgraph-multi-agent"


@router.post(
    "/",
    summary="AI 智能对话（同步）",
    description="提交消息给 AI 智能体进行对话。支持三种执行模式：ReAct（默认）、Plan-Execute（先规划后执行）和 Multi-Agent（多智能体协作）。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def chat_endpoint(req: ChatRequest):
    sid = req.session_id or str(uuid.uuid4())

    # Resolve mode and alias legacy names to LangGraph equivalents
    mode = req.mode
    _MODE_ALIASES_SYNC = {
        "react": "langgraph-react",
        "plan-execute": "langgraph-plan-execute",
        "multi-agent": "langgraph-multi-agent",
        "sourcing": "langgraph-sourcing",
    }

    if mode == "auto":
        from app.graphs.router import router as intent_router
        mode = intent_router.route(req.message).value
    else:
        mode = _MODE_ALIASES_SYNC.get(mode, mode)

    # Sync endpoint uses legacy agent_chat for all modes (kept as fallback)
    reply = await asyncio.to_thread(agent_chat, sid, req.message)
    return {"reply": reply, "session_id": sid}


@router.post(
    "/stream",
    summary="AI 智能对话（流式 SSE）",
    description="以 Server-Sent Events 流式返回 AI 智能体的对话响应。支持三种执行模式，首先返回 session_id，随后逐事件推送对话内容。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def chat_stream_endpoint(req: ChatRequest, request: Request):
    """Streaming chat endpoint using SSE (Server-Sent Events)."""
    sid = req.session_id or str(uuid.uuid4())
    user_id = getattr(request.state, "user_id", "")
    from app.services.user_preference import build_preference_context
    pref_ctx = build_preference_context(user_id) if user_id else ""

    # Resolve mode: auto → intent router, otherwise use explicit mode
    mode = req.mode
    if mode == "auto":
        from app.graphs.router import router as intent_router
        mode = intent_router.route(req.message).value

    # Map legacy mode names to LangGraph equivalents
    _MODE_ALIASES = {
        "react": "langgraph-react",
        "plan-execute": "langgraph-plan-execute",
        "multi-agent": "langgraph-multi-agent",
        "sourcing": "langgraph-sourcing",
    }
    mode = _MODE_ALIASES.get(mode, mode)

    # Choose execution mode
    if mode == "langgraph-react":
        stream_fn = _langgraph_react_stream
    elif mode == "langgraph-plan-execute":
        stream_fn = _langgraph_plan_execute_stream
    elif mode == "langgraph-multi-agent":
        stream_fn = _langgraph_supervisor_stream
    elif mode == "langgraph-sourcing":
        stream_fn = _langgraph_sourcing_stream
    else:
        stream_fn = _langgraph_react_stream

    async def event_generator():
        # Send session_id first
        yield f"event: session\ndata: {sid}\n\n"

        # Programmatic clarification check
        from app.services.clarification import detect_clarification_needed
        clar = detect_clarification_needed(req.message)
        if clar:
            yield f"event: clarification\ndata: {json.dumps({'message': clar.message, 'missing': clar.missing}, ensure_ascii=False)}\n\n"
            return

        # Stream the chat response
        async for event in stream_fn(sid, req.message, pref_ctx):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
