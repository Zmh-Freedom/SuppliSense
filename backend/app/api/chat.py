import json
import uuid

from pydantic import BaseModel

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])


async def _langgraph_react_stream(session_id: str, message: str, preference_context: str = ""):
    """LangGraph ReAct 模式流式输出。"""
    from app.graphs.react_graph import build_react_graph
    from app.graphs.streaming import stream_react_graph
    from app.services.agent import _load_history

    graph = build_react_graph(preference_context)
    history = _load_history(session_id)
    # 传入 config 用于 Human-in-the-Loop 恢复
    run_config = {"configurable": {"thread_id": session_id}}
    async for event in stream_react_graph(graph, message, session_id, history, run_config):
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


async def _langgraph_agent_supervisor_stream(
    session_id: str, message: str, preference_context: str = ""
):
    """Agent Supervisor 组合寻源与风险任务流式输出。"""
    del preference_context

    from app.graphs.agent_supervisor.graph import build_agent_supervisor_graph
    from app.graphs.streaming import stream_agent_supervisor_graph

    graph = build_agent_supervisor_graph()
    run_config = {"configurable": {"thread_id": session_id}}
    async for event in stream_agent_supervisor_graph(
        graph, message, session_id, run_config
    ):
        yield event


async def _langgraph_parallel_stream(session_id: str, message: str, preference_context: str = ""):
    """LangGraph Parallel 并行多 Agent Map-Reduce 流式输出。"""
    from app.graphs.parallel_graph import stream_parallel_graph
    from app.services.agent import _load_history

    history = _load_history(session_id)
    async for event in stream_parallel_graph(message, session_id, history, preference_context):
        yield event


async def _langgraph_react_reflection_stream(session_id: str, message: str, preference_context: str = ""):
    """LangGraph ReAct + Self-Reflection 流式输出。"""
    from app.graphs.react_graph import build_react_graph_with_reflection
    from app.graphs.streaming import stream_react_graph
    from app.services.agent import _load_history

    graph = build_react_graph_with_reflection(preference_context)
    history = _load_history(session_id)
    run_config = {"configurable": {"thread_id": session_id}}
    async for event in stream_react_graph(graph, message, session_id, history, run_config):
        yield event


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    mode: str = "auto"  # "auto" | "react" | "plan-execute" | "multi-agent" | "parallel" | "react-reflection" | "sourcing" | "agent-supervisor"


class ResumeRequest(BaseModel):
    session_id: str
    approved: bool = True


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
    user_id = getattr(request.state, "user_id", "")
    from app.domains.auth.preferences import build_preference_context
    pref_ctx = build_preference_context(user_id) if user_id else ""

    # Map legacy mode names to LangGraph equivalents
    _MODE_ALIASES = {
        "react": "langgraph-react",
        "plan-execute": "langgraph-plan-execute",
        "multi-agent": "langgraph-multi-agent",
        "sourcing": "langgraph-sourcing",
        "parallel": "langgraph-parallel",
        "react-reflection": "langgraph-react-reflection",
        "agent-supervisor": "langgraph-agent-supervisor",
    }

    async def event_generator():
        # Send session_id first (before any blocking routing/classification)
        yield f"event: session\ndata: {sid}\n\n"

        # Resolve mode: auto → intent router, otherwise use explicit mode
        mode = req.mode
        if mode == "auto":
            from app.graphs.router import router as intent_router
            mode = intent_router.route(req.message).value
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
        elif mode == "langgraph-agent-supervisor":
            stream_fn = _langgraph_agent_supervisor_stream
        elif mode == "langgraph-parallel":
            stream_fn = _langgraph_parallel_stream
        elif mode == "langgraph-react-reflection":
            stream_fn = _langgraph_react_reflection_stream
        else:
            stream_fn = _langgraph_react_stream

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
    from app.graphs.interrupt_store import pop

    paused = pop(req.session_id)
    if not paused:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="无暂停的会话，可能已过期")

    graph = paused["graph"]
    config = paused["config"]
    mode = paused["mode"]
    user_message = paused["user_message"]

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

            async for event in graph.astream_events(cmd, config, version="v2"):
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

            yield f"event: done\ndata: {json.dumps({'answer': ''}, ensure_ascii=False)}\n\n"

        except Exception as e:
            from app.graphs import format_llm_error
            msg = format_llm_error(e)
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
