"""LangGraph Supervisor Graph — 多智能体协作编排。

使用 supervisor 模式，子 Agent 的 LLM 和工具节点直接嵌入顶层图，
确保 on_chat_model_stream 事件传播到外层 SSE 流。
"""

import json
from typing import Annotated, Any, AsyncGenerator, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.core.config import settings
from app.tools import TOOLS_LIST

SUPERVISOR_SYSTEM_PROMPT = (
    "你是任务分配专家。根据用户查询，选择最合适的分析 Agent。\n"
    "可用 Agent：risk（风险评估）、sentiment（舆情分析）、compliance（合规检查）。\n"
    '输出 JSON：{"next": "agent_name"} 或 {"next": "FINISH"}。\n'
    "简单问候返回 FINISH。"
)

# ---- Sub-agent tool subsets ----

_RISK_TOOLS = [t for t in TOOLS_LIST if t.name in (
    "search_company", "assess_risk", "esg_assessment", "predict_risk", "macro_risk",
)]

_SENTIMENT_TOOLS = [t for t in TOOLS_LIST if t.name in (
    "search_company", "sentiment_analysis", "check_alert",
)]

_COMPLIANCE_TOOLS = [t for t in TOOLS_LIST if t.name in (
    "search_company", "check_sanctions", "assess_risk",
)]

RISK_PROMPT = """你是风险评估专家。

职责：
1. 评估供应商综合风险（财务、ESG、宏观、预测）
2. 使用 search_company 确认企业全称后调用 assess_risk
3. 上市公司要分析财报，debt_ratio=0 表示数据缺失不要解读为低负债
4. 回答简洁，300 字以内，中文"""

SENTIMENT_PROMPT = """你是舆情分析专家。

职责：
1. 分析企业舆情情感倾向（正面/负面/中性）
2. 检查企业预警变化
3. 使用 search_company 确认企业全称后调用 sentiment_analysis
4. 回答简洁，300 字以内，中文"""

COMPLIANCE_PROMPT = """你是合规检查专家。

职责：
1. 筛查企业是否在国际制裁/黑名单中（OFAC实体清单、失信等）
2. 评估企业合规风险
3. 使用 search_company 确认企业全称后调用 check_sanctions
4. 回答简洁，300 字以内，中文"""


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class SupervisorState(TypedDict):
    """Supervisor graph 的共享状态。"""

    messages: Annotated[list, add_messages]
    next: str
    current_agent: str


# ---------------------------------------------------------------------------
# LLM builder
# ---------------------------------------------------------------------------

def _build_domain_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        temperature=0,
    )


# ---------------------------------------------------------------------------
# Supervisor node
# ---------------------------------------------------------------------------

async def supervisor_node(state: SupervisorState) -> dict[str, Any]:
    """LLM 路由节点：决定下一步调用哪个 Agent 或结束。"""
    llm = _build_domain_llm()

    messages = [SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT)] + list(
        state["messages"]
    )

    response: AIMessage = await llm.ainvoke(messages)
    text = response.content.strip()

    next_target = _parse_next(text)

    return {"next": next_target, "current_agent": ""}


def _parse_next(text: str) -> str:
    """从 LLM 回复中解析 next 字段。支持裸 JSON 和 ```json 围栏。"""
    for candidate in _json_candidates(text):
        try:
            obj = json.loads(candidate)
            nxt = obj.get("next", "")
            if nxt in ("risk", "sentiment", "compliance", "FINISH"):
                return nxt
        except json.JSONDecodeError:
            continue

    lower = text.lower()
    for keyword in ("risk", "sentiment", "compliance"):
        if keyword in lower:
            return keyword

    return "FINISH"


def _json_candidates(text: str) -> list[str]:
    """从文本中提取可能的 JSON 片段。"""
    candidates: list[str] = []
    candidates.append(text)
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:]
            candidates.append(part.strip())
    return candidates


# ---------------------------------------------------------------------------
# Sub-agent LLM nodes (first-class in the top-level graph for streaming)
# ---------------------------------------------------------------------------

async def risk_agent_node(state: SupervisorState) -> dict[str, Any]:
    """风险评估 LLM 节点。"""
    llm = _build_domain_llm().bind_tools(_RISK_TOOLS)
    messages = [SystemMessage(content=RISK_PROMPT)] + list(state["messages"])
    response = await llm.ainvoke(messages)
    return {"messages": [response], "current_agent": "risk"}


async def sentiment_agent_node(state: SupervisorState) -> dict[str, Any]:
    """舆情分析 LLM 节点。"""
    llm = _build_domain_llm().bind_tools(_SENTIMENT_TOOLS)
    messages = [SystemMessage(content=SENTIMENT_PROMPT)] + list(state["messages"])
    response = await llm.ainvoke(messages)
    return {"messages": [response], "current_agent": "sentiment"}


async def compliance_agent_node(state: SupervisorState) -> dict[str, Any]:
    """合规检查 LLM 节点。"""
    llm = _build_domain_llm().bind_tools(_COMPLIANCE_TOOLS)
    messages = [SystemMessage(content=COMPLIANCE_PROMPT)] + list(state["messages"])
    response = await llm.ainvoke(messages)
    return {"messages": [response], "current_agent": "compliance"}


# ---------------------------------------------------------------------------
# Sub-agent routing (tool loop vs return to supervisor)
# ---------------------------------------------------------------------------

def route_risk_agent(state: SupervisorState) -> str:
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "risk_tools"
    return "supervisor"


def route_sentiment_agent(state: SupervisorState) -> str:
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "sentiment_tools"
    return "supervisor"


def route_compliance_agent(state: SupervisorState) -> str:
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "compliance_tools"
    return "supervisor"


# ---------------------------------------------------------------------------
# Supervisor routing
# ---------------------------------------------------------------------------

def route_supervisor(state: SupervisorState) -> str:
    """根据 supervisor 的 next 字段决定下一个节点。"""
    nxt = state.get("next", "FINISH")
    if nxt == "risk":
        return "risk_agent"
    if nxt == "sentiment":
        return "sentiment_agent"
    if nxt == "compliance":
        return "compliance_agent"
    return END


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_supervisor_graph():
    """构建并编译 supervisor 图。

    流程（flat 结构）：
        supervisor → (risk_agent | sentiment_agent | compliance_agent | END)
        risk_agent ⇄ risk_tools (工具循环), risk_agent → supervisor (回答完成)
        sentiment_agent ⇄ sentiment_tools, sentiment_agent → supervisor
        compliance_agent ⇄ compliance_tools, compliance_agent → supervisor
    """
    graph = StateGraph(SupervisorState)

    # Supervisor
    graph.add_node("supervisor", supervisor_node)

    # Risk sub-agent chain
    graph.add_node("risk_agent", risk_agent_node)
    graph.add_node("risk_tools", ToolNode(_RISK_TOOLS))

    # Sentiment sub-agent chain
    graph.add_node("sentiment_agent", sentiment_agent_node)
    graph.add_node("sentiment_tools", ToolNode(_SENTIMENT_TOOLS))

    # Compliance sub-agent chain
    graph.add_node("compliance_agent", compliance_agent_node)
    graph.add_node("compliance_tools", ToolNode(_COMPLIANCE_TOOLS))

    graph.set_entry_point("supervisor")

    # Supervisor routing
    graph.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {
            "risk_agent": "risk_agent",
            "sentiment_agent": "sentiment_agent",
            "compliance_agent": "compliance_agent",
            END: END,
        },
    )

    # Risk: agent <-> tools, agent -> supervisor when done
    graph.add_conditional_edges(
        "risk_agent", route_risk_agent,
        {"risk_tools": "risk_tools", "supervisor": "supervisor"},
    )
    graph.add_edge("risk_tools", "risk_agent")

    # Sentiment: same pattern
    graph.add_conditional_edges(
        "sentiment_agent", route_sentiment_agent,
        {"sentiment_tools": "sentiment_tools", "supervisor": "supervisor"},
    )
    graph.add_edge("sentiment_tools", "sentiment_agent")

    # Compliance: same pattern
    graph.add_conditional_edges(
        "compliance_agent", route_compliance_agent,
        {"compliance_tools": "compliance_tools", "supervisor": "supervisor"},
    )
    graph.add_edge("compliance_tools", "compliance_agent")

    return graph.compile()


# ---------------------------------------------------------------------------
# SSE streaming
# ---------------------------------------------------------------------------

def _sse_event(event_type: str, data: dict) -> str:
    """Format data as SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_supervisor_graph(
    user_message: str,
    session_id: str,
    history: list[dict] | None = None,
    preference_context: str = "",
) -> AsyncGenerator[str, None]:
    """运行 supervisor 图并 yield SSE 事件。

    Events: thinking, agent_selection, agent_start, agent_complete,
            answer_chunk, done, error
    """
    from app.services.agent import _save_turn
    from app.graphs.context import build_input_messages

    input_messages = await build_input_messages(history or [], user_message)

    if preference_context:
        input_messages.insert(0, SystemMessage(content=preference_context))

    graph = build_supervisor_graph()
    full_answer = ""
    current_agent: str | None = None
    agent_answer_accumulator: dict[str, str] = {}

    try:
        async for event in graph.astream_events(
            {"messages": input_messages, "next": "", "current_agent": ""},
            version="v2",
        ):
            kind = event.get("event", "")

            # ---- supervisor 节点产出 next ----
            if kind == "on_chain_end" and event.get("name") == "supervisor":
                output = event.get("data", {}).get("output", {})
                nxt = output.get("next", "FINISH") if isinstance(output, dict) else "FINISH"
                if nxt == "FINISH":
                    yield _sse_event("agent_selection", {
                        "agents": [],
                        "reasoning": "任务完成",
                    })
                else:
                    current_agent = nxt
                    yield _sse_event("thinking", {"message": f"选择 {nxt} Agent..."})
                    yield _sse_event("agent_selection", {
                        "agents": [nxt],
                        "reasoning": f"路由到 {nxt} Agent",
                    })

            # ---- 子 Agent LLM 节点开始 ----
            elif kind == "on_chain_start" and event.get("name") in (
                "risk_agent", "sentiment_agent", "compliance_agent",
            ):
                agent_name = event["name"].replace("_agent", "")
                current_agent = agent_name
                agent_answer_accumulator[agent_name] = ""
                yield _sse_event("agent_start", {"agent": agent_name})

            # ---- 子 Agent LLM 节点完成 ----
            elif kind == "on_chain_end" and event.get("name") in (
                "risk_agent", "sentiment_agent", "compliance_agent",
            ):
                agent_name = event["name"].replace("_agent", "")
                accumulated = agent_answer_accumulator.get(agent_name, "")
                if accumulated:
                    full_answer = accumulated
                yield _sse_event("agent_complete", {"agent": agent_name})

            # ---- LLM 流式 token（所有顶层 LLM 节点均可见）----
            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and chunk.content:
                    if current_agent and current_agent in agent_answer_accumulator:
                        agent_answer_accumulator[current_agent] += chunk.content
                    full_answer = chunk.content  # also track globally
                    yield _sse_event("answer_chunk", {"text": chunk.content})

            # ---- LLM 完整响应（非流式兜底）----
            elif kind == "on_chat_model_end":
                if not full_answer:
                    output = event.get("data", {}).get("output")
                    if output and hasattr(output, "content") and output.content:
                        full_answer = output.content
                        yield _sse_event("answer_chunk", {"text": full_answer})

        # 保存对话历史
        if full_answer:
            _save_turn(session_id, user_message, full_answer)

        yield _sse_event("done", {"answer": full_answer})

    except Exception as e:
        yield _sse_event("error", {"message": f"Supervisor 图执行错误: {str(e)}"})
