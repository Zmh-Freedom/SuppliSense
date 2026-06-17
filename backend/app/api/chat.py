import asyncio
import uuid

from pydantic import BaseModel

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.agent import chat as agent_chat
from app.services.agent import chat_stream as agent_chat_stream
from app.services.agent import chat_stream_with_plan as agent_chat_stream_with_plan
from app.services.agent import chat_stream_with_agents as agent_chat_stream_with_agents

router = APIRouter()


async def _langgraph_react_stream(session_id: str, message: str):
    """LangGraph ReAct 模式流式输出。"""
    from app.graphs.react_graph import build_react_graph
    from app.graphs.streaming import stream_react_graph
    from app.services.agent import _load_history

    graph = build_react_graph()
    history = _load_history(session_id)
    async for event in stream_react_graph(graph, message, session_id, history):
        yield event


async def _langgraph_plan_execute_stream(session_id: str, message: str):
    """LangGraph Plan-Execute 模式流式输出。"""
    from app.graphs.plan_execute_graph import stream_plan_execute_graph
    from app.services.agent import _load_history

    history = _load_history(session_id)
    async for event in stream_plan_execute_graph(message, session_id, history):
        yield event


async def _langgraph_supervisor_stream(session_id: str, message: str):
    """LangGraph Supervisor 多智能体模式流式输出。"""
    from app.graphs.supervisor_graph import stream_supervisor_graph
    from app.services.agent import _load_history

    history = _load_history(session_id)
    async for event in stream_supervisor_graph(message, session_id, history):
        yield event


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    mode: str = "react"  # "react", "plan-execute", "multi-agent", "langgraph-react"


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
async def chat_stream_endpoint(req: ChatRequest):
    """Streaming chat endpoint using SSE (Server-Sent Events)."""
    sid = req.session_id or str(uuid.uuid4())

    # Choose execution mode
    if req.mode == "plan-execute":
        stream_fn = agent_chat_stream_with_plan
    elif req.mode == "multi-agent":
        stream_fn = agent_chat_stream_with_agents
    elif req.mode == "langgraph-react":
        stream_fn = _langgraph_react_stream
    elif req.mode == "langgraph-plan-execute":
        stream_fn = _langgraph_plan_execute_stream
    elif req.mode == "langgraph-multi-agent":
        stream_fn = _langgraph_supervisor_stream
    else:
        stream_fn = agent_chat_stream

    async def event_generator():
        # Send session_id first
        yield f"event: session\ndata: {sid}\n\n"
        # Stream the chat response
        async for event in stream_fn(sid, req.message):
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
