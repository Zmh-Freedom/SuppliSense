"""Sourcing subgraph — 智能寻源流程编排。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import TypedDict

from app.graphs.sourcing_risk_v2.checkpointer import (
    compile_sourcing_risk_graph,
    get_sourcing_risk_checkpointer,
)
from app.core.logging import get_logger

logger = get_logger()

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


class SourcingState(TypedDict):
    messages: Annotated[list, add_messages]
    request_input: dict | None
    candidates: list[str]
    risk_results: dict[str, dict]
    final_results: list[dict]
    error: str | None
    conversation_state: dict
    current_task: dict


SOURCING_SYSTEM = """你是一个采购寻源助手。用户想通过自然语言描述采购需求来寻找供应商。

你的职责是：
1. 从用户消息中提取采购需求（品类、规格、预算、地域等）
2. 调用 create_sourcing_request 创建寻源请求
3. 调用 search_suppliers 执行搜索并获取排序结果
4. 以清晰的格式呈现候选供应商，包括匹配分、风险分和推荐理由

如果用户问“有哪些正式供应商 / 已准入供应商 / 正式供应商数量或清单”，必须直接调用 list_formal_suppliers；这不是采购寻源，不得创建品类为“综合”等默认需求。

如果有供应商结果，请以表格形式展示关键信息；外部候选优先展示官网/来源链接、电话、邮箱（没有则明确写“待核验/未找到”），然后是风险要点。
如果无匹配结果，告知用户并建议扩充供应商库。
用户说“执行/确认/同意准入”时，必须调用 select_external_supplier_candidate；优先传入 candidate_id，否则传入上下文中的 supplier_name，不能只输出文字结论。"""


_SOURCING_TOOLS = None
_FORMAL_DIRECTORY_TOKENS = ("正式供应商", "已准入供应商", "合格供应商")
_DIRECTORY_REQUEST_TOKENS = ("哪些", "哪几", "清单", "列表", "多少", "几个", "有谁")


def _get_sourcing_tools():
    """延迟初始化寻源工具子集，避免模块导入时拉起完整工具链。"""
    global _SOURCING_TOOLS
    if _SOURCING_TOOLS is None:
        from app.tools import (
            list_formal_suppliers,
            create_sourcing_request,
            search_suppliers,
            select_sourcing_result,
            discover_web_suppliers,
            select_external_supplier_candidate,
        )
        _SOURCING_TOOLS = [
            list_formal_suppliers,
            create_sourcing_request,
            search_suppliers,
            select_sourcing_result,
            discover_web_suppliers,
            select_external_supplier_candidate,
        ]
    return _SOURCING_TOOLS


def _is_formal_supplier_directory_query(state: SourcingState) -> bool:
    """Whether the latest user message is a formal supplier directory request."""
    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None
    if not isinstance(last_message, HumanMessage):
        return False
    content = str(last_message.content)
    if not any(token in content for token in _FORMAL_DIRECTORY_TOKENS):
        return False
    return any(token in content for token in _DIRECTORY_REQUEST_TOKENS)


def _format_formal_supplier_directory(result: dict) -> str:
    """Format a read-only supplier directory result without another LLM turn."""
    items = result.get("items", [])
    total = result.get("total", 0)
    if not items:
        return "当前没有查询到正式（已准入）的供应商。"

    rows = ["| 供应商代码 | 供应商名称 | 品类 | 供货区域 |", "| --- | --- | --- | --- |"]
    for item in items:
        def display(value: object) -> str:
            return str(value or "—").replace("|", "、").replace("\n", " ")

        rows.append(
            "| {supplier_code} | {supplier_name} | {categories} | {regions} |".format(
                supplier_code=display(item.get("supplier_code")),
                supplier_name=display(item.get("supplier_name")),
                categories=display("、".join(item.get("categories") or [])),
                regions=display("、".join(item.get("regions") or [])),
            )
        )

    suffix = "" if total == len(items) else f"（以下展示前 {len(items)} 家）"
    return (
        f"当前共有 {total} 家正式（已准入）供应商{suffix}，数据来自已同步的供应商主数据快照：\n\n"
        + "\n".join(rows)
    )


def build_sourcing_graph(checkpointer: AsyncPostgresSaver | None = None):
    graph = StateGraph(SourcingState)

    graph.add_node("sourcing_agent", _sourcing_agent)
    graph.add_node("sourcing_tools", _sourcing_tools)

    graph.set_entry_point("sourcing_agent")
    graph.add_conditional_edges("sourcing_agent", _route_after_agent, {
        "tools": "sourcing_tools",
        END: END,
    })
    graph.add_edge("sourcing_tools", "sourcing_agent")

    return compile_sourcing_risk_graph(graph, checkpointer=checkpointer)


async def _sourcing_agent(state: SourcingState):
    from app.graphs import build_shared_llm

    if _is_formal_supplier_directory_query(state):
        # This is a deterministic read-only query. Returning the directory here
        # prevents the model from inventing a default category or skipping the
        # result after the required lookup completes.
        from app.domains.sourcing.supplier_repo import list_formal_suppliers

        result = await asyncio.to_thread(list_formal_suppliers, 20)
        return {"messages": [AIMessage(content=_format_formal_supplier_directory(result))]}

    llm = build_shared_llm()
    llm_with_tools = llm.bind_tools(_get_sourcing_tools())

    msgs = list(state["messages"])
    if not any(isinstance(m, SystemMessage) for m in msgs):
        msgs = [SystemMessage(content=SOURCING_SYSTEM)] + msgs

    response = await llm_with_tools.ainvoke(msgs)
    return {"messages": [response]}


def _sourcing_tools(state: SourcingState):
    from langgraph.prebuilt import ToolNode

    node = ToolNode(_get_sourcing_tools())
    return node.invoke(state)


def _route_after_agent(state: SourcingState):
    msgs = state["messages"]
    last = msgs[-1] if msgs else None
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


async def stream_sourcing_graph(
    session_id: str,
    message: str,
    preference_context: str = "",
    execution_context: dict | None = None,
):
    """SSE stream wrapper for the sourcing subgraph."""

    from app.graphs.agent_core.adapter import (
        load_execution_context,
        save_execution_turn,
    )
    from app.graphs.streaming import (
        _checkpoint_messages,
        _has_unresolved_tool_calls,
        _sse_event,
        _workflow_status,
    )
    from app.graphs.context import build_input_messages

    context = execution_context or load_execution_context(session_id, message)
    checkpointer = await get_sourcing_risk_checkpointer()
    graph = build_sourcing_graph(checkpointer)
    from app.graphs.chat_checkpoint import chat_checkpoint_config

    run_config = chat_checkpoint_config(session_id, "sourcing")
    checkpoint_messages = await _checkpoint_messages(graph, run_config)
    if _has_unresolved_tool_calls(checkpoint_messages):
        yield _sse_event("error", {
            "message": "上一轮会话仍有未完成的工具执行，请等待其结束或重新打开会话后再试。"
        })
        return

    # Like ReAct, the persisted namespace already contains completed turns.
    # Replaying Mongo history here duplicates prior messages and can invalidate
    # tool-call ordering on the next model request.
    input_history = [] if checkpoint_messages else context["history"]
    msgs = await build_input_messages(
        input_history, message, context["references"], context
    )
    if preference_context:
        msgs.insert(0, SystemMessage(content=preference_context))
    msgs.insert(0, SystemMessage(content=SOURCING_SYSTEM))
    full_answer = ""
    discovered_references: list[dict] = []

    try:
        yield _workflow_status("running", "understand", "正在分析寻源需求...")
        async for event in graph.astream_events(
            {
                "messages": msgs,
                "request_input": None,
                "candidates": [],
                "final_results": [],
                "error": None,
                "conversation_state": context["conversation_state"],
                "current_task": context["current_task"],
            },
            config=run_config,
            version="v2",
        ):
            kind = event["event"]

            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if hasattr(chunk, "content") and chunk.content:
                    full_answer += chunk.content
                    yield _sse_event("answer_chunk", {"text": chunk.content})

            elif kind == "on_chat_model_end":
                # streaming=False 时 token 不会通过 on_chat_model_stream 下发，
                # 需要从 end 事件取完整内容
                output = event.get("data", {}).get("output")
                content = output.content if output and hasattr(output, "content") else ""
                if content:
                    full_answer = content
                    yield _sse_event("answer_chunk", {"text": content})

            elif kind == "on_chain_end" and event.get("name") in {"_sourcing_agent", "sourcing_agent"}:
                # Deterministic read-only branches do not emit chat-model
                # events. Extract their final AI message from the node output
                # so the SSE client receives the same answer it would receive
                # from an LLM-backed branch.
                output = event.get("data", {}).get("output", {})
                messages = output.get("messages", []) if isinstance(output, dict) else []
                answer = messages[-1] if messages else None
                content = answer.content if answer and hasattr(answer, "content") else ""
                if content and not full_answer:
                    full_answer = content
                    yield _sse_event("answer_chunk", {"text": content})

            elif kind == "on_tool_start":
                tool_name = event["name"]
                yield _sse_event("tool_call", {"tool": tool_name, "args": event["data"].get("input", {})})
                yield _workflow_status("running", "executing", f"正在执行寻源工具：{tool_name}")
                if tool_name == "search_suppliers":
                    yield _sse_event("retrieving", {"message": "正在检索候选供应商..."})
                    yield _sse_event("assessing", {"message": "正在并行评估风险..."})

            elif kind == "on_tool_end":
                output = event["data"].get("output", "")
                result = getattr(output, "content", output)
                yield _sse_event("tool_result", {"tool": event["name"], "result": str(result)[:500]})
                yield _workflow_status("running", "evidence", f"已收到寻源结果：{event['name']}")
                from app.graphs.agent_core.adapter import collect_supplier_references

                discovered_references = collect_supplier_references(
                    discovered_references, result, event["name"]
                )

    except Exception as e:
        logger.exception(
            "sourcing_graph_stream_failed",
            session_id=session_id,
            error=str(e),
        )
        from app.graphs import format_llm_error
        yield _workflow_status("failed", "executing", "寻源 Agent 执行失败")
        yield _sse_event("error", {"message": format_llm_error(e)})
        return

    try:
        from app.graphs.agent_core.adapter import collect_supplier_references

        discovered_references = collect_supplier_references(
            discovered_references, full_answer, "Agent 回答"
        )
        save_execution_turn(session_id, message, full_answer, discovered_references)
    except Exception:
        pass
    if discovered_references:
        yield _sse_event("references", {"items": discovered_references})
    yield _workflow_status("completed", "completed", "本轮 Agent 工作流已完成")
    yield _sse_event("done", {"answer": full_answer})
