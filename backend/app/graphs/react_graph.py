"""LangGraph ReAct agent graph — 替代 agent.py 中的手写 ReAct 循环。"""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.graphs import build_shared_llm
from app.tools import TOOLS_LIST

SYSTEM_PROMPT = """你是采购风险分析专家。

核心规则：
1. **必须先调用工具获取数据，严禁凭空编造数据**
2. 回答要简洁，控制在 300 字以内
3. 使用中文回答

图表输出（当回答中包含对比数据、趋势变化或评分分布时，嵌入图表让分析更直观）：
- 在回答中使用 ```chart 代码块输出 JSON 格式的图表数据
- 支持类型：line（折线图）、bar（柱状图）、radar（雷达图）、pie（饼图）
- 示例：
```chart
{"type": "bar", "title": "风险评分对比", "data": [{"name": "海康威视", "value": 35}, {"name": "大华股份", "value": 42}]}
```
- 仅在数据包含 2 个以上数据点时使用图表
- 不要重复已在文字中详细列出的数字，用图表直观展示即可

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
- 会话上下文给出多个供应商且用户说“这些企业/上述企业/它们/推荐的供应商”时：不得再询问企业名；应逐家调用对应工具，并按企业汇总结果
- 生成报告：用 generate_report
- 查趋势：指定企业用 analyze_trend，查监控清单全部趋势用 analyze_watchlist_trend
- 对比企业：用 compare_companies
- 查财务：用 query_financials
- 定时报告：用 manage_scheduled_report
- 找供应商/寻源：用 create_sourcing_request 创建需求，再用 search_suppliers 搜索候选
- 供应商不足时：优先用 discover_web_suppliers 联网发现待核验候选；只有用户明确确认并允许入库时，才考虑 expand_supplier_library
- 本地寻源候选准入：必须用 select_sourcing_result(result_id, action="apply_access")
- 联网候选准入：只有 identity_status=exact 时用 select_external_supplier_candidate(candidate_id, action="apply_access")，不得把 candidate_id 当作 result_id
- 用户说“执行/确认/同意准入”时，必须按结构化候选类型立即调用对应工具；不得只回复“同意准入”或再次建议确认

业务规则：
- assess_risk 已含财报数据，上市公司要分析财报
- debt_ratio=0 表示数据缺失（港股），不要解读为低负债
- in_watchlist=true 表示已在监控，不要建议"加入监控"
- 综合问题可调多个工具
- 搜不到就告知用户
- 不同工具返回的数据如有矛盾，直接指出差异，不要自行编造理由解释
- 工具返回 error 字段时，告知用户具体哪个环节出错，并尝试用其他工具替代（如 search_company 失败可尝试 tianyancha_query）

澄清规则（当用户意图不明确时，不要猜测，直接询问）：
- 缺少企业名称时："请问您想分析哪家公司？"
- 缺少分析维度时："您关注哪些方面？风险评分、财务指标、舆情、还是全部？"
- 缺少时间范围时："您想看最近多久的数据？"
- 用户说"报告"但未指定格式时，默认生成 Excel"""


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    conversation_state: dict
    current_task: dict


_ACCESS_REQUEST_TOKENS = ("准入", "申请入库", "成为合格供应商")


def _forced_access_call(state: AgentState) -> AIMessage | None:
    """Hard-route one unambiguous admission request to its typed selection tool."""
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if not isinstance(last, HumanMessage) or not any(
        token in str(last.content) for token in _ACCESS_REQUEST_TOKENS
    ):
        return None
    active_suppliers = [
        reference
        for reference in state.get("conversation_state", {}).get("active_suppliers", [])
        if isinstance(reference, dict) and reference.get("name")
    ]
    explicit_candidates = [
        candidate for candidate in active_suppliers
        if str(candidate["name"]) in str(last.content)
    ]
    candidates = explicit_candidates or active_suppliers
    if len(candidates) != 1:
        return None
    candidate = candidates[0]
    if candidate.get("candidate_type") == "local" and candidate.get("result_id"):
        return AIMessage(content="", tool_calls=[{
            "name": "select_sourcing_result",
            "args": {
                "result_id": candidate["result_id"],
                "action": "apply_access",
            },
            "id": "forced-local-access",
            "type": "tool_call",
        }])
    if not (
        candidate.get("candidate_id")
        and candidate.get("candidate_type") == "external"
        and candidate.get("identity_status") == "exact"
    ):
        return None
    return AIMessage(content="", tool_calls=[{
        "name": "select_external_supplier_candidate",
        "args": {
            "candidate_id": candidate["candidate_id"],
            "supplier_name": candidate.get("name", ""),
            "action": "apply_access",
        },
        "id": "forced-external-access",
        "type": "tool_call",
    }])


def _forced_external_access_call(state: AgentState) -> AIMessage | None:
    """Backward-compatible alias retained for existing integrations and tests."""
    return _forced_access_call(state)


# 模块级 LLM 单例，避免每次请求创建新连接
_llm_instance = None


def _get_llm():
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = build_shared_llm().bind_tools(TOOLS_LIST)
    return _llm_instance


def build_react_graph(
    preference_context: str = "", checkpointer: Any = None,
):
    """编译 ReAct 图（带 system prompt 注入，可选偏好上下文）。"""
    prompt = SYSTEM_PROMPT
    if preference_context:
        prompt = preference_context + "\n\n" + SYSTEM_PROMPT

    llm = _get_llm()
    tool_node = ToolNode(TOOLS_LIST)

    async def agent(state: AgentState):
        forced_call = _forced_access_call(state)
        if forced_call:
            return {"messages": [forced_call]}
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

    compiled = graph.compile(checkpointer=checkpointer)

    class ReactGraphWithSystemPrompt:
        def __init__(self, g):
            self._graph = g

        async def astream_events(self, input_data, **kwargs):
            if isinstance(input_data, dict):
                messages = input_data.get("messages", [])
                if not messages or not isinstance(messages[0], SystemMessage):
                    messages = [SystemMessage(content=prompt)] + messages
                    input_data = {**input_data, "messages": messages}
            async for event in self._graph.astream_events(input_data, **kwargs):
                yield event

        async def ainvoke(self, input_data, **kwargs):
            if isinstance(input_data, dict):
                messages = input_data.get("messages", [])
                if not messages or not isinstance(messages[0], SystemMessage):
                    messages = [SystemMessage(content=prompt)] + messages
                    input_data = {**input_data, "messages": messages}
            return await self._graph.ainvoke(input_data, **kwargs)

    return ReactGraphWithSystemPrompt(compiled)


def build_react_graph_with_reflection(
    preference_context: str = "", checkpointer: Any = None,
):
    """编译带 Self-Reflection 的 ReAct 图。

    在 agent 产生 final answer 后，reflector 审查输出质量（幻觉、一致性、完整性）。
    发现问题时反馈给 agent 重新生成，最多 1 次纠正循环。

    与 build_react_graph 的区别：
    - State 扩展 reflection_feedback / reflection_count 字段
    - agent 无 tool_calls → reflector（而非 END）
    - reflector 可路由回 agent 纠正
    """
    from app.graphs.reflection import build_reflector_node, route_after_reflector

    prompt = SYSTEM_PROMPT
    if preference_context:
        prompt = preference_context + "\n\n" + SYSTEM_PROMPT

    class ReactReflectionState(TypedDict):
        messages: Annotated[list, add_messages]
        reflection_feedback: str
        reflection_count: int
        conversation_state: dict
        current_task: dict

    llm = _get_llm()
    tool_node = ToolNode(TOOLS_LIST)
    reflector_fn = build_reflector_node()

    async def agent(state: ReactReflectionState):
        # 如果有 reflection 反馈，追加为上下文
        feedback = state.get("reflection_feedback", "")
        msgs = list(state["messages"])
        if feedback:
            from langchain_core.messages import HumanMessage
            msgs.append(HumanMessage(
                content=f"审核反馈（请根据以下意见修正你的回答，不要重复之前的错误）：\n{feedback}"
            ))
        response = await llm.ainvoke(msgs)
        return {"messages": [response]}

    def should_continue(state: ReactReflectionState):
        last_msg = state["messages"][-1]
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            return "tools"
        # 无 tool_calls → 进入 reflector 审查
        return "reflector"

    graph = StateGraph(ReactReflectionState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tool_node)
    graph.add_node("reflector", reflector_fn)

    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent", should_continue,
        {"tools": "tools", "reflector": "reflector"},
    )
    graph.add_edge("tools", "agent")
    graph.add_conditional_edges(
        "reflector", route_after_reflector,
        {"agent": "agent", END: END},
    )

    compiled = graph.compile(checkpointer=checkpointer)

    class ReactReflectionGraphWithSystemPrompt:
        """注入 system prompt + 初始化 reflection 字段。"""

        def __init__(self, g):
            self._graph = g

        async def astream_events(self, input_data, **kwargs):
            if isinstance(input_data, dict):
                messages = input_data.get("messages", [])
                if not messages or not isinstance(messages[0], SystemMessage):
                    messages = [SystemMessage(content=prompt)] + messages
                input_data = {
                    **input_data,
                    "messages": messages,
                    "reflection_feedback": input_data.get("reflection_feedback", ""),
                    "reflection_count": input_data.get("reflection_count", 0),
                }
            async for event in self._graph.astream_events(input_data, **kwargs):
                yield event

        async def ainvoke(self, input_data, **kwargs):
            if isinstance(input_data, dict):
                messages = input_data.get("messages", [])
                if not messages or not isinstance(messages[0], SystemMessage):
                    messages = [SystemMessage(content=prompt)] + messages
                input_data = {
                    **input_data,
                    "messages": messages,
                    "reflection_feedback": input_data.get("reflection_feedback", ""),
                    "reflection_count": input_data.get("reflection_count", 0),
                }
            return await self._graph.ainvoke(input_data, **kwargs)

    return ReactReflectionGraphWithSystemPrompt(compiled)
