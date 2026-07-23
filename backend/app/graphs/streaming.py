"""LangGraph 流式输出适配为 SSE 事件格式，保持与前端兼容。"""

import json
from typing import AsyncGenerator


def _sse_event(event_type: str, data: dict) -> str:
    """Format data as SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_react_graph(
    graph,
    user_message: str,
    session_id: str,
    history: list[dict] | None = None,
    run_config: dict | None = None,
) -> AsyncGenerator[str, None]:
    """运行 ReAct 图并 yield SSE 事件。

    Events: thinking, tool_call, tool_result, answer_chunk,
            approval_required, done, error

    当工具触发 interrupt() 时，发射 approval_required 事件并暂停。
    通过 resume 端点恢复后继续流式输出。
    """
    from app.graphs.context import build_input_messages

    input_messages = await build_input_messages(history or [], user_message)

    full_answer = ""
    tool_call_count = 0
    config = run_config or {}

    yield _sse_event("thinking", {"message": "正在分析您的问题..."})

    try:
        async for event in graph.astream_events(
            {"messages": input_messages},
            config,
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
                if len(result) > 2000:
                    result = result[:2000] + "...(截断)"
                yield _sse_event("tool_result", {"tool": tool_name, "result": result})

                # 自动注入图表事件
                from app.graphs.chart_data import _try_auto_chart
                chart = _try_auto_chart(tool_name, result)
                if chart:
                    yield _sse_event("chart_data", chart)

            # LLM 完成（非流式响应或工具调用后的回答）
            elif kind == "on_chat_model_end":
                output = event.get("data", {}).get("output")
                content = output.content if output and hasattr(output, "content") else ""
                if content:
                    full_answer = content
                    yield _sse_event("answer_chunk", {"text": content})

            # Reflector 发现问题时通知
            elif kind == "on_chain_end" and event.get("name") == "reflector":
                output = event.get("data", {}).get("output", {})
                if isinstance(output, dict):
                    feedback = output.get("reflection_feedback", "")
                    if feedback:
                        yield _sse_event("thinking", {"message": f"正在优化回答: {feedback}"})

        # 保存对话历史
        if full_answer:
            from app.services.agent import _save_turn

            _save_turn(session_id, user_message, full_answer)

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
            )

            yield _sse_event("approval_required", {
                "message": interrupt_data.get("message", "确认此操作？"),
                "tool": interrupt_data.get("tool", ""),
                "args": interrupt_data.get("args", {}),
                "session_id": session_id,
            })
            return

        from app.graphs import format_llm_error

        yield _sse_event("error", {"message": format_llm_error(e)})


def _is_graph_interrupt(exc: Exception) -> bool:
    """判断异常是否为 LangGraph 中断。"""
    exc_name = type(exc).__name__
    return exc_name in ("GraphInterrupt", "NodeInterrupt", "Interrupt")


def _extract_interrupt_data(exc: Exception) -> dict:
    """从 GraphInterrupt 异常中提取中断数据。"""
    try:
        # GraphInterrupt 通常有 args[0] 作为中断值
        if hasattr(exc, "args") and exc.args:
            data = exc.args[0]
            if isinstance(data, dict):
                return data
            return {"message": str(data)}
    except Exception:
        pass

    return {"message": str(exc)}
