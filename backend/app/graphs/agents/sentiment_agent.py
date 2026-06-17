"""LangGraph 舆情分析子 Agent — 用于 Supervisor 模式。"""

from typing import Annotated, TypedDict

from langchain_core.messages import SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.tools import TOOLS_LIST

# 舆情分析专用工具子集
_SENTIMENT_TOOLS = [t for t in TOOLS_LIST if t.name in (
    "search_company",
    "sentiment_analysis",
    "check_alert",
)]

SYSTEM_PROMPT = """你是舆情分析专家。

职责：
1. 分析企业舆情情感倾向（正面/负面/中性）
2. 检查企业预警变化
3. 使用 search_company 确认企业全称后调用 sentiment_analysis
4. 回答简洁，300 字以内，中文"""


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def build_sentiment_agent():
    """构建并编译舆情分析子 Agent 图。"""
    from app.graphs.agents import build_domain_llm

    llm = build_domain_llm().bind_tools(_SENTIMENT_TOOLS)
    tool_node = ToolNode(_SENTIMENT_TOOLS)

    async def agent(state: AgentState):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(state["messages"])
        response = await llm.ainvoke(messages)
        return {"messages": [response]}

    def should_continue(state: AgentState):
        last_msg = state["messages"][-1]
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()
