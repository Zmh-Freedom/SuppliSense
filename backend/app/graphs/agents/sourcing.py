"""Sourcing subgraph — 智能寻源流程编排。"""

import json
from typing import Annotated, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import TypedDict

from app.core.config import settings


class SourcingState(TypedDict):
    messages: Annotated[list, add_messages]
    request_input: dict | None
    candidates: list[str]
    risk_results: dict[str, dict]
    final_results: list[dict]
    error: str | None


SOURCING_SYSTEM = """你是一个采购寻源助手。用户想通过自然语言描述采购需求来寻找供应商。

你的职责是：
1. 从用户消息中提取采购需求（品类、规格、预算、地域等）
2. 调用 create_sourcing_request 创建寻源请求
3. 调用 search_suppliers 执行搜索并获取排序结果
4. 以清晰的格式呈现候选供应商，包括匹配分、风险分和推荐理由

如果有供应商结果，请以表格形式展示关键信息，然后是风险要点。
如果无匹配结果，告知用户并建议扩充供应商库。"""


def build_sourcing_graph():
    graph = StateGraph(SourcingState)

    graph.add_node("sourcing_agent", _sourcing_agent)
    graph.add_node("sourcing_tools", _sourcing_tools)

    graph.set_entry_point("sourcing_agent")
    graph.add_conditional_edges("sourcing_agent", _route_after_agent, {
        "tools": "sourcing_tools",
        END: END,
    })
    graph.add_edge("sourcing_tools", "sourcing_agent")

    return graph.compile()


def _sourcing_agent(state: SourcingState):
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        temperature=0,
    )

    from app.tools import create_sourcing_request, search_suppliers, select_sourcing_result
    sourcing_tools = [create_sourcing_request, search_suppliers, select_sourcing_result]
    llm_with_tools = llm.bind_tools(sourcing_tools)

    msgs = list(state["messages"])
    if not any(isinstance(m, SystemMessage) for m in msgs):
        msgs = [SystemMessage(content=SOURCING_SYSTEM)] + msgs

    response = llm_with_tools.invoke(msgs)
    return {"messages": [response]}


def _sourcing_tools(state: SourcingState):
    from langgraph.prebuilt import ToolNode
    from app.tools import create_sourcing_request, search_suppliers, select_sourcing_result

    node = ToolNode([create_sourcing_request, search_suppliers, select_sourcing_result])
    return node.invoke(state)


def _route_after_agent(state: SourcingState):
    msgs = state["messages"]
    last = msgs[-1] if msgs else None
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


async def stream_sourcing_graph(session_id: str, message: str, preference_context: str = ""):
    """SSE stream wrapper for the sourcing subgraph."""

    from app.services.agent import _load_history, _save_turn
    from app.graphs.streaming import _sse_event
    from app.graphs.context import build_input_messages

    history = _load_history(session_id)
    msgs = await build_input_messages(history or [], message)
    if preference_context:
        msgs.insert(0, SystemMessage(content=preference_context))
    msgs.insert(0, SystemMessage(content=SOURCING_SYSTEM))

    graph = build_sourcing_graph()
    full_answer = ""

    try:
        async for event in graph.astream_events(
            {"messages": msgs, "request_input": None, "candidates": [], "final_results": [], "error": None},
            version="v2",
        ):
            kind = event["event"]

            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if hasattr(chunk, "content") and chunk.content:
                    full_answer += chunk.content
                    yield _sse_event("answer_chunk", {"text": chunk.content})

            elif kind == "on_tool_start":
                tool_name = event["name"]
                yield _sse_event("tool_call", {"tool": tool_name, "args": event["data"].get("input", {})})
                if tool_name == "search_suppliers":
                    yield _sse_event("retrieving", {"message": "正在检索候选供应商..."})
                    yield _sse_event("assessing", {"message": "正在并行评估风险..."})

            elif kind == "on_tool_end":
                yield _sse_event("tool_result", {"tool": event["name"], "result": str(event["data"].get("output", ""))[:500]})

    except Exception as e:
        yield _sse_event("error", {"message": str(e)})
        return

    yield _sse_event("done", {"answer": full_answer})

    try:
        _save_turn(session_id, message, full_answer)
    except Exception:
        pass
