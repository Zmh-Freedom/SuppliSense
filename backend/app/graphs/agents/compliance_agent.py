"""LangGraph 合规检查子 Agent — 用于 Supervisor 模式。"""

from typing import Annotated, TypedDict

from langchain_core.messages import SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.tools import TOOLS_LIST

# 合规检查专用工具子集
_COMPLIANCE_TOOLS = [t for t in TOOLS_LIST if t.name in (
    "search_company",
    "check_sanctions",
    "assess_risk",
)]

SYSTEM_PROMPT = """你是合规检查专家。

职责：
1. 筛查企业是否在国际制裁/黑名单中（OFAC实体清单、失信等）
2. 评估企业合规风险
3. 使用 search_company 确认企业全称后调用 check_sanctions
4. 回答简洁，300 字以内，中文"""


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def build_compliance_agent():
    """构建并编译合规检查子 Agent 图。"""
    from app.graphs.agents import build_domain_llm

    llm = build_domain_llm().bind_tools(_COMPLIANCE_TOOLS)
    tool_node = ToolNode(_COMPLIANCE_TOOLS)

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
