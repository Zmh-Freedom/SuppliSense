"""LangGraph Parallel Supervisor Graph — 并行多 Agent Map-Reduce 编排。

使用 Send() API 实现并行 fan-out：supervisor 决定调用哪些 agent，
所有 agent 同时执行各自的 ReAct 工具循环，最后由 synthesizer 合成统一回答。

与 supervisor_graph.py（串行轮询）互补，适合多维度并行分析场景。
"""

import json
from typing import Annotated, Any, AsyncGenerator, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Send

from app.graphs import build_shared_llm
from app.tools import TOOLS_LIST


# ---------------------------------------------------------------------------
# Tool subsets (same as supervisor_graph.py)
# ---------------------------------------------------------------------------

_RISK_TOOLS = [t for t in TOOLS_LIST if t.name in (
    "search_company", "assess_risk", "esg_assessment", "predict_risk", "macro_risk",
    "create_sourcing_request", "search_suppliers", "select_sourcing_result",
    "find_alternatives", "expand_supplier_library",
    "discover_web_suppliers",
    "analyze_trend", "analyze_watchlist_trend", "compare_companies",
    "get_watchlist", "check_alert",
)]

_SENTIMENT_TOOLS = [t for t in TOOLS_LIST if t.name in (
    "search_company", "sentiment_analysis", "check_alert",
)]

_COMPLIANCE_TOOLS = [t for t in TOOLS_LIST if t.name in (
    "search_company", "check_sanctions", "assess_risk",
)]


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

PARALLEL_SUPERVISOR_PROMPT = """你是任务分配专家。根据用户查询，决定需要调用哪些分析 Agent。

可用 Agent：
- risk：风险评估、趋势分析、监控清单、寻源推荐、ESG 评估、财务分析
- sentiment：舆情分析、情感倾向
- compliance：合规检查、制裁筛查

规则：
1. 简单问候返回空列表
2. 单维度问题只返回该维度的 Agent（如"海康威视风险"→["risk"]）
3. 多维度问题返回多个 Agent（如"全面评估"→["risk", "sentiment", "compliance"]）
4. 明确提到"舆情"时包含 sentiment，提到"合规/制裁"时包含 compliance

输出 JSON：{"agents": ["risk", "sentiment"]} 或 {"agents": []}"""

RISK_PROMPT = """你是风险评估与寻源专家。

职责：
1. 评估供应商综合风险（财务、ESG、宏观、预测）
2. 使用 search_company 确认企业全称后调用 assess_risk
3. 上市公司要分析财报，debt_ratio=0 表示数据缺失不要解读为低负债
4. 用户需要找供应商时，用 create_sourcing_request 创建需求，再调用 search_suppliers 搜索
5. 监控清单相关：趋势分析调用 analyze_watchlist_trend，查看清单调用 get_watchlist
6. 回答简洁，300 字以内，中文"""

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

SYNTHESIZER_PROMPT = """你是综合分析专家。请根据各领域 Agent 的独立分析结果，整合成一份统一的风险分析报告。

要求：
1. 按重要程度排列各维度发现
2. 如果不同 Agent 的结论有矛盾，直接指出差异，不要编造理由
3. 补充各维度之间的关联（如：舆情负面可能导致未来风险上升）
4. 综合结论和可操作建议

图表输出（当报告中包含对比数据、趋势变化或评分分布时，嵌入图表让分析更直观）：
- 用 ```chart 代码块输出 JSON，支持 line（折线图）、bar（柱状图）、radar（雷达图）、pie（饼图）
- 示例：```chart\\n{"type": "radar", "title": "综合风险维度", "data": [{"dim": "财务", "score": 35}, {"dim": "司法", "score": 52}, {"dim": "经营", "score": 28}]}\\n```
- 仅在数据包含 2 个以上数据点时使用，不要重复文字已详细列出的数字

输出中文，格式清晰。"""


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _merge_dicts(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    """自定义 reducer：合并两个 dict，right 优先。"""
    merged = dict(left)
    merged.update(right)
    return merged


class ParallelState(TypedDict):
    """并行 multi-agent 图的共享状态。"""

    messages: Annotated[list, add_messages]
    agent_tasks: list[str]
    agent_outputs: Annotated[dict[str, str], _merge_dicts]
    current_agent: str
    reflection_feedback: str
    reflection_count: int
    conversation_state: dict
    current_task: dict


# ---------------------------------------------------------------------------
# LLM builders (module-level singletons)
# ---------------------------------------------------------------------------

_domain_llm = None


def _build_domain_llm():
    global _domain_llm
    if _domain_llm is None:
        _domain_llm = build_shared_llm()
    return _domain_llm


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

def _json_candidates(text: str) -> list[str]:
    candidates: list[str] = [text]
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:]
            candidates.append(part.strip())
    return candidates


def _parse_agents(text: str) -> list[str]:
    valid = {"risk", "sentiment", "compliance"}
    for candidate in _json_candidates(text):
        try:
            obj = json.loads(candidate)
            agents = obj.get("agents", [])
            result = [a for a in agents if a in valid]
            if result:
                return result
        except json.JSONDecodeError:
            continue
    return []


# ---------------------------------------------------------------------------
# Supervisor node
# ---------------------------------------------------------------------------

async def supervisor_node(state: ParallelState) -> dict[str, Any]:
    """LLM 路由节点：决定并行调用哪些 Agent。"""
    llm = _build_domain_llm()

    messages = [SystemMessage(content=PARALLEL_SUPERVISOR_PROMPT)] + list(
        state["messages"]
    )

    response: AIMessage = await llm.ainvoke(messages)
    agents = _parse_agents(str(response.content).strip())

    return {"agent_tasks": agents, "current_agent": ""}


# ---------------------------------------------------------------------------
# Agent nodes
# ---------------------------------------------------------------------------

async def risk_agent_node(state: ParallelState) -> dict[str, Any]:
    """风险评估 LLM 节点。"""
    llm = _build_domain_llm().bind_tools(_RISK_TOOLS)
    messages = [SystemMessage(content=RISK_PROMPT)] + list(state["messages"])
    response = await llm.ainvoke(messages)

    result: dict[str, Any] = {"messages": [response], "current_agent": "risk"}

    # store final answer (no tool_calls) in agent_outputs
    if not (hasattr(response, "tool_calls") and response.tool_calls):
        result["agent_outputs"] = {"risk": str(response.content)}

    return result


async def sentiment_agent_node(state: ParallelState) -> dict[str, Any]:
    """舆情分析 LLM 节点。"""
    llm = _build_domain_llm().bind_tools(_SENTIMENT_TOOLS)
    messages = [SystemMessage(content=SENTIMENT_PROMPT)] + list(state["messages"])
    response: AIMessage = await llm.ainvoke(messages)

    result: dict[str, Any] = {"messages": [response], "current_agent": "sentiment"}

    if not (hasattr(response, "tool_calls") and response.tool_calls):
        result["agent_outputs"] = {"sentiment": str(response.content)}

    return result


async def compliance_agent_node(state: ParallelState) -> dict[str, Any]:
    """合规检查 LLM 节点。"""
    llm = _build_domain_llm().bind_tools(_COMPLIANCE_TOOLS)
    messages = [SystemMessage(content=COMPLIANCE_PROMPT)] + list(state["messages"])
    response: AIMessage = await llm.ainvoke(messages)

    result: dict[str, Any] = {"messages": [response], "current_agent": "compliance"}

    if not (hasattr(response, "tool_calls") and response.tool_calls):
        result["agent_outputs"] = {"compliance": str(response.content)}

    return result


# ---------------------------------------------------------------------------
# Agent routing (tool loop → synthesizer when done)
# ---------------------------------------------------------------------------

def route_risk_agent(state: ParallelState) -> str:
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "risk_tools"
    return "synthesizer"


def route_sentiment_agent(state: ParallelState) -> str:
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "sentiment_tools"
    return "synthesizer"


def route_compliance_agent(state: ParallelState) -> str:
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "compliance_tools"
    return "synthesizer"


# ---------------------------------------------------------------------------
# Fan-out: supervisor → parallel agents via Send()
# ---------------------------------------------------------------------------

def fan_out_to_agents(state: ParallelState) -> "list[Send] | str":
    """条件边函数：将 supervisor 的决策转为并行 Send 列表。

    返回 list[Send] → LangGraph 并行执行所有分支。
    返回 "synthesizer" → 无 agent 需要调用，直接合成。
    """
    tasks = state.get("agent_tasks", [])

    if not tasks:
        return "synthesizer"

    sends: list[Send] = []
    for task in tasks:
        # 每个分支传入当前 messages + 标记 current_agent
        sends.append(Send(
            node=f"{task}_agent",
            arg={
                "messages": list(state["messages"]),
                "agent_tasks": [],
                "agent_outputs": {},
                "current_agent": task,
                "reflection_feedback": "",
                "reflection_count": 0,
                "conversation_state": state.get("conversation_state", {}),
                "current_task": state.get("current_task", {}),
            },
        ))
    return sends


# ---------------------------------------------------------------------------
# Synthesizer node
# ---------------------------------------------------------------------------

async def synthesizer_node(state: ParallelState) -> dict[str, Any]:
    """合并所有 Agent 的输出，生成统一的分析报告。"""
    llm = _build_domain_llm()

    outputs = state.get("agent_outputs", {})

    # 构建 agent 分析上下文
    context_parts: list[str] = []
    for agent_name in ("risk", "sentiment", "compliance"):
        if agent_name in outputs:
            context_parts.append(
                f"## {agent_name} Agent 分析结果\n{outputs[agent_name]}"
            )

    agent_context = "\n\n".join(context_parts) if context_parts else "（无 Agent 分析结果）"

    # 提取用户原始问题
    user_query = ""
    for m in reversed(state.get("messages", [])):
        if isinstance(m, HumanMessage):
            user_query = str(m.content)
            break

    # 如果有 reflection 反馈，追加修正指令
    feedback = state.get("reflection_feedback", "")
    correction_instruction = ""
    if feedback:
        correction_instruction = (
            f"\n\n⚠️ 上一轮输出被审核驳回，请根据以下反馈修正：\n{feedback}\n"
            "请重新整合各 Agent 分析结果，修正指出的问题。"
        )

    messages = [
        SystemMessage(content=SYNTHESIZER_PROMPT),
        HumanMessage(
            content=f"用户原始问题：{user_query}\n\n各 Agent 独立分析结果：\n{agent_context}{correction_instruction}"
        ),
    ]

    response = await llm.ainvoke(messages)
    return {"messages": [response], "current_agent": ""}


# ---------------------------------------------------------------------------
# Synthesizer → reflector routing
# ---------------------------------------------------------------------------

def route_synthesizer_to_reflector(state: ParallelState) -> str:
    """synthesizer 完成后进入 reflector 审查。"""
    return "reflector"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_parallel_graph(enable_reflection: bool = True):
    """构建并编译并行 Map-Reduce 图。

    流程：
        START → supervisor
                  │
                  ├─[fan_out via Send]── risk_agent ⇄ risk_tools ─┐
                  ├─[fan_out via Send]── sentiment_agent ⇄ sentiment_tools ─┤
                  └─[fan_out via Send]── compliance_agent ⇄ compliance_tools ─┘
                                                 │
                                            synthesizer
                                                 │
                                            [reflector]
                                             │    └─(feedback)→ synthesizer
                                            END

    Send 并行原理：
    - fan_out_to_agents 返回 list[Send]，LangGraph 为每个 Send 创建独立分支
    - 每个 agent 在自己的分支中执行 ReAct 工具循环
    - 所有分支路由到 synthesizer 后，LangGraph 等待全部完成再执行 synthesizer
    - synthesizer 的 state 合并了所有分支的消息（通过 add_messages reducer）
    """
    graph = StateGraph(ParallelState)

    # Supervisor
    graph.add_node("supervisor", supervisor_node)

    # Agent nodes
    graph.add_node("risk_agent", risk_agent_node)
    graph.add_node("risk_tools", ToolNode(_RISK_TOOLS))
    graph.add_node("sentiment_agent", sentiment_agent_node)
    graph.add_node("sentiment_tools", ToolNode(_SENTIMENT_TOOLS))
    graph.add_node("compliance_agent", compliance_agent_node)
    graph.add_node("compliance_tools", ToolNode(_COMPLIANCE_TOOLS))

    # Synthesizer
    graph.add_node("synthesizer", synthesizer_node)

    graph.set_entry_point("supervisor")

    # Fan-out: supervisor → parallel agents via Send()
    graph.add_conditional_edges(
        "supervisor",
        fan_out_to_agents,
        {"synthesizer": "synthesizer"},  # fallback when 0 agents
    )

    # Risk: agent ↔ tools → synthesizer
    graph.add_conditional_edges(
        "risk_agent", route_risk_agent,
        {"risk_tools": "risk_tools", "synthesizer": "synthesizer"},
    )
    graph.add_edge("risk_tools", "risk_agent")

    # Sentiment: agent ↔ tools → synthesizer
    graph.add_conditional_edges(
        "sentiment_agent", route_sentiment_agent,
        {"sentiment_tools": "sentiment_tools", "synthesizer": "synthesizer"},
    )
    graph.add_edge("sentiment_tools", "sentiment_agent")

    # Compliance: agent ↔ tools → synthesizer
    graph.add_conditional_edges(
        "compliance_agent", route_compliance_agent,
        {"compliance_tools": "compliance_tools", "synthesizer": "synthesizer"},
    )
    graph.add_edge("compliance_tools", "compliance_agent")

    # Reflection loop (optional)
    if enable_reflection:
        from app.graphs.reflection import build_reflector_node, route_after_reflector

        graph.add_node("reflector", build_reflector_node())
        graph.add_conditional_edges(
            "synthesizer", route_synthesizer_to_reflector,
            {"reflector": "reflector"},
        )
        graph.add_conditional_edges(
            "reflector", route_after_reflector,
            {"agent": "synthesizer", END: END},
        )
    else:
        graph.add_edge("synthesizer", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# SSE streaming
# ---------------------------------------------------------------------------

def _sse_event(event_type: str, data: dict) -> str:
    """Format data as SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_parallel_graph(
    user_message: str,
    session_id: str,
    history: list[dict] | None = None,
    preference_context: str = "",
    references: list[dict] | None = None,
    execution_context: dict | None = None,
) -> AsyncGenerator[str, None]:
    """运行并行 Map-Reduce 图并 yield SSE 事件。

    Events: agent_selection, agent_start, agent_complete,
            answer_chunk (with optional agent tag), tool_call, tool_result,
            done, error
    """
    from app.graphs.agent_core.adapter import build_execution_context, save_execution_turn
    from app.graphs.context import build_input_messages

    resolved_context = execution_context or build_execution_context(
        session_id=session_id,
        user_message=user_message,
        history=history or [],
        references=references or [],
    )
    input_messages = await build_input_messages(
        history or [], user_message, references, resolved_context
    )

    if preference_context:
        input_messages.insert(0, SystemMessage(content=preference_context))

    graph = build_parallel_graph(enable_reflection=True)
    agent_answers: dict[str, str] = {}
    all_text = ""

    try:
        from app.graphs.streaming import _workflow_status

        yield _workflow_status("running", "understand", "正在规划并行 Agent 任务...")
        async for event in graph.astream_events(
            {
                "messages": input_messages,
                "agent_tasks": [],
                "agent_outputs": {},
                "current_agent": "",
                "reflection_feedback": "",
                "reflection_count": 0,
            },
            version="v2",
        ):
            kind = event.get("event", "")
            node_name = event.get("metadata", {}).get("langgraph_node", "")
            run_name = event.get("name", "")

            # ---- supervisor 完成：emit agent_selection ----
            if kind == "on_chain_end" and run_name == "supervisor":
                output = event.get("data", {}).get("output", {})
                tasks = (
                    output.get("agent_tasks", [])
                    if isinstance(output, dict)
                    else []
                )
                if tasks:
                    yield _workflow_status("running", "planning", f"已选择 {len(tasks)} 个专业 Agent")
                    yield _sse_event("agent_selection", {
                        "agents": tasks,
                        "reasoning": f"并行调用 {', '.join(tasks)} Agent",
                    })
                else:
                    yield _sse_event("agent_selection", {
                        "agents": [],
                        "reasoning": "直接回答，无需 Agent",
                    })

            # ---- agent 节点开始 ----
            elif kind == "on_chain_start" and node_name in (
                "risk_agent", "sentiment_agent", "compliance_agent",
            ):
                agent_name = node_name.replace("_agent", "")
                yield _workflow_status("running", "executing", f"正在执行 {agent_name} Agent")
                yield _sse_event("agent_start", {"agent": agent_name})

            # ---- agent 节点完成 ----
            elif kind == "on_chain_end" and node_name in (
                "risk_agent", "sentiment_agent", "compliance_agent",
            ):
                agent_name = node_name.replace("_agent", "")
                answer = agent_answers.get(agent_name, "")
                yield _sse_event("agent_complete", {
                    "agent": agent_name,
                    "summary": answer[:200] if answer else "",
                })
                yield _workflow_status("running", "evidence", f"{agent_name} Agent 已返回结果")

            # ---- LLM token 流式 ----
            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and chunk.content:
                    text = str(chunk.content)
                    # 识别 token 来源
                    if node_name.endswith("_agent"):
                        agent = node_name.replace("_agent", "")
                        agent_answers[agent] = agent_answers.get(agent, "") + text
                        yield _sse_event("answer_chunk", {"text": text, "agent": agent})
                    elif node_name == "synthesizer":
                        all_text += text
                        yield _sse_event("answer_chunk", {"text": text})

            # ---- LLM 完整响应（非流式兜底）----
            elif kind == "on_chat_model_end":
                output = event.get("data", {}).get("output")
                content = (
                    str(output.content)
                    if output and hasattr(output, "content")
                    else ""
                )
                if content and node_name == "synthesizer":
                    all_text += content
                    yield _sse_event("answer_chunk", {"text": content})

            # ---- 工具调用 ----
            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                if tool_name and tool_name not in (
                    "supervisor", "risk_agent", "sentiment_agent",
                    "compliance_agent", "synthesizer", "reflector",
                ):
                    agent = node_name.replace("_agent", "") if node_name else ""
                    yield _sse_event("tool_call", {
                        "tool": tool_name,
                        "args": event.get("data", {}).get("input", {}),
                        "agent": agent,
                    })

            # ---- 工具调用结束 + 自动图表 ----
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

                from app.graphs.chart_data import _try_auto_chart
                chart = _try_auto_chart(tool_name, result)
                if chart:
                    yield _sse_event("chart_data", chart)

            # ---- reflector 发现问题时通知用户 ----
            elif kind == "on_chain_end" and node_name == "reflector":
                output = event.get("data", {}).get("output", {})
                feedback = (
                    output.get("reflection_feedback", "")
                    if isinstance(output, dict)
                    else ""
                )
                if feedback:
                    yield _sse_event("thinking", {
                        "message": f"正在优化回答: {feedback}",
                    })

        # 保存对话历史
        discovered_references = list(references or [])
        if all_text:
            from app.graphs.agent_core.adapter import collect_supplier_references

            discovered_references = collect_supplier_references(
                discovered_references, all_text, "Agent 回答"
            )
            save_execution_turn(
                session_id, user_message, all_text, discovered_references
            )

        if discovered_references:
            yield _sse_event("references", {"items": discovered_references})

        yield _workflow_status("completed", "completed", "本轮 Agent 工作流已完成")
        yield _sse_event("done", {"answer": all_text})

    except Exception as e:
        from app.graphs import format_llm_error

        yield _workflow_status("failed", "decision", "Agent 工作流执行失败")
        yield _sse_event("error", {"message": format_llm_error(e)})
