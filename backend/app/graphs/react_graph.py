"""LangGraph ReAct agent graph — 替代 agent.py 中的手写 ReAct 循环。"""

import os
from typing import Annotated, TypedDict

from langchain_core.messages import SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI

from app.tools import TOOLS_LIST

SYSTEM_PROMPT = """你是采购风险分析专家。

核心规则：
1. **必须先调用工具获取数据，严禁凭空编造数据**
2. 回答要简洁，控制在 300 字以内
3. 使用中文回答

分析流程：
- 看风险：先用 search_company 搜全名，再用 assess_risk
- 看ESG：用 esg_assessment
- 看传染：用 contagion_analysis
- 看舆情：用 sentiment_analysis
- 看预测：用 predict_risk
- 看宏观：用 macro_risk
- 找替代：用 find_alternatives
- 情景模拟：用 scenario_simulate
- 制裁筛查：用 check_sanctions
- 知识库检索：用 knowledge_search
- 要对比多家：先 get_watchlist，再逐个 assess_risk

业务规则：
- assess_risk 已含财报数据，上市公司要分析财报
- debt_ratio=0 表示数据缺失（港股），不要解读为低负债
- in_watchlist=true 表示已在监控，不要建议"加入监控"
- 综合问题可调多个工具
- 搜不到就告知用户"""


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def _build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        api_key=os.getenv("LLM_API_KEY", ""),
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        temperature=0,
    ).bind_tools(TOOLS_LIST)


def _build_graph() -> StateGraph:
    """构建 ReAct 图。"""
    llm = _build_llm()
    tool_node = ToolNode(TOOLS_LIST)

    async def agent(state: AgentState):
        response = await llm.ainvoke(state["messages"])
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

    return graph


def build_react_graph():
    """编译 ReAct 图（带 system prompt 注入）。"""
    graph = _build_graph().compile()

    class ReactGraphWithSystemPrompt:
        """包装图，自动注入 system prompt。"""

        def __init__(self, compiled_graph):
            self._graph = compiled_graph

        async def astream_events(self, input_data, **kwargs):
            messages = input_data.get("messages", [])
            # 如果第一条不是 system message，注入 system prompt
            if not messages or not isinstance(messages[0], SystemMessage):
                messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages
                input_data = {**input_data, "messages": messages}
            async for event in self._graph.astream_events(input_data, **kwargs):
                yield event

        async def ainvoke(self, input_data, **kwargs):
            messages = input_data.get("messages", [])
            if not messages or not isinstance(messages[0], SystemMessage):
                messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages
                input_data = {**input_data, "messages": messages}
            return await self._graph.ainvoke(input_data, **kwargs)

    return ReactGraphWithSystemPrompt(graph)
