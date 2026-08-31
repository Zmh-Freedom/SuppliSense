"""LangGraph 风险评估子 Agent — 用于 Supervisor 模式。"""

from typing import Annotated, TypedDict

from langchain_core.messages import SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.tools import TOOLS_LIST

# 风险评估专用工具子集
_RISK_TOOLS = [t for t in TOOLS_LIST if t.name in (
    "search_company",
    "assess_risk",
    "assess_business_risk",
    "esg_assessment",
    "predict_risk",
    "macro_risk",
)]

SYSTEM_PROMPT = """你是风险评估专家。

职责：
1. 评估供应商综合风险（财务、ESG、宏观、预测）及商务风险 P0
2. 使用 search_company 确认企业全称后调用 assess_risk
3. 上市公司要分析财报，debt_ratio=0 表示数据缺失不要解读为低负债
4. 商务风险 P0 只在真实交易快照存在时形成供应依赖结论；没有真实数据时必须说明数据缺失，不能把合成数据当正式证据
4. 回答简洁，300 字以内，中文"""


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def build_risk_agent():
    """构建并编译风险评估子 Agent 图。"""
    from app.graphs.agents import build_domain_llm

    llm = build_domain_llm().bind_tools(_RISK_TOOLS)
    tool_node = ToolNode(_RISK_TOOLS)

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
