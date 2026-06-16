"""LangGraph 流式输出适配为 SSE 事件格式，保持与前端兼容。"""

import json
from typing import AsyncGenerator

from langchain_core.messages import HumanMessage


def _sse_event(event_type: str, data: dict) -> str:
    """Format data as SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_react_graph(
    graph,
    user_message: str,
    session_id: str,
    history: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """运行 ReAct 图并 yield SSE 事件。

    Events: thinking, tool_call, tool_result, answer_chunk, done, error
    """
    # 构建输入消息
    input_messages = []
    if history:
        for m in history:
            input_messages.append({"role": m["role"], "content": m["content"]})
    input_messages.append(HumanMessage(content=user_message))

    full_answer = ""
    tool_call_count = 0

    try:
        async for event in graph.astream_events(
            {"messages": input_messages},
            version="v2",
        ):
            kind = event.get("event", "")

            # LLM token 级流式输出
            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and chunk.content:
                    full_answer += chunk.content
                    yield _sse_event("answer_chunk", {"text": chunk.content})

            # 工具调用开始
            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                tool_input = event.get("data", {}).get("input", {})
                yield _sse_event("tool_call", {"tool": tool_name, "args": tool_input})
                tool_call_count += 1

            # 工具调用结束
            elif kind == "on_tool_end":
                tool_name = event.get("name", "")
                output = event.get("data", {}).get("output", "")
                if isinstance(output, str):
                    result = output
                else:
                    result = json.dumps(output, ensure_ascii=False, default=str)
                yield _sse_event("tool_result", {"tool": tool_name, "result": result})

            # LLM 完成（非流式 chunk 的完整响应）
            elif kind == "on_chat_model_end":
                # 如果没有通过 stream 拿到 content，从 end event 取
                if not full_answer:
                    output = event.get("data", {}).get("output")
                    if output and hasattr(output, "content") and output.content:
                        full_answer = output.content
                        yield _sse_event("answer_chunk", {"text": full_answer})

        # 保存对话历史
        if full_answer:
            from app.services.agent import _save_turn
            _save_turn(session_id, user_message, full_answer)

        yield _sse_event("done", {"answer": full_answer})

    except Exception as e:
        yield _sse_event("error", {"message": f"LangGraph 执行错误: {str(e)}"})
