"""LangGraph Plan-Execute agent graph — 替代 agent.py 中的手写 Plan-Execute 循环。"""

import asyncio
import json
import os
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langchain_openai import ChatOpenAI

from app.tools import TOOLS_LIST

# 工具名称到工具函数的映射
_TOOLS_MAP = {t.name: t for t in TOOLS_LIST}


class PlanExecuteState(TypedDict):
    """Plan-Execute 图的状态定义。"""
    input: str  # 用户原始问题
    plan: list[str]  # 剩余计划步骤（每个是工具调用描述）
    past_steps: list[tuple[str, str]]  # (步骤描述, 执行结果) 已完成步骤
    response: str | None  # 最终答案（完成时设置）


def _build_llm() -> ChatOpenAI:
    """构建 LLM 实例。"""
    return ChatOpenAI(
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        api_key=os.getenv("LLM_API_KEY", ""),
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        temperature=0,
    )


def _parse_json_from_response(content: str) -> dict:
    """从 LLM 响应中解析 JSON，处理 markdown 代码块。"""
    content = content.strip()
    # 处理 markdown 代码块
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        content = "\n".join(lines).strip()
    return json.loads(content)


PLANNER_PROMPT = """你是采购风险分析的规划专家。根据用户问题，生成一个执行计划。

可用工具：
- search_company: 根据关键词搜索企业全称
- assess_risk: 评估供应商风险（含财报）
- check_alert: 查看企业预警变化
- get_watchlist: 获取监控清单
- add_to_watchlist: 将企业加入监控清单
- remove_from_watchlist: 将企业从监控清单移除
- esg_assessment: 评估ESG风险
- contagion_analysis: 分析风险传染路径
- sentiment_analysis: 分析舆情情感
- predict_risk: 预测未来风险恶化概率
- macro_risk: 分析宏观风险
- find_alternatives: 推荐替代供应商
- scenario_simulate: 模拟情景影响
- check_sanctions: 筛查制裁/黑名单
- knowledge_search: 知识库检索
- generate_report: 生成风险评估报告（Excel/HTML）
- analyze_trend: 分析风险评分历史趋势
- compare_companies: 对比多家企业风险状况
- query_financials: 查询企业财务指标
- manage_scheduled_report: 管理定时报告任务

规则：
1. 如果用户问的是某企业风险，先搜索确认全称，再评估风险
2. 步骤描述要具体，包含工具名和参数
3. 输出纯 JSON 格式: {"steps": ["步骤1描述", "步骤2描述"]}

示例输入：海康威视的风险怎么样
示例输出：{"steps": ["用 search_company 搜索'海康威视'获取企业全称", "用 assess_risk 评估该企业的综合风险"]}"""

EXECUTOR_PROMPT = """你是一个工具执行助手。根据步骤描述，确定要调用的工具和参数。

可用工具列表：
{tools_desc}

请输出纯 JSON 格式: {{"tool": "工具名", "args": {{"参数名": "参数值"}}}}"""

REPLANNER_PROMPT = """你是采购风险分析的规划专家。根据已完成的步骤和剩余计划，决定下一步。

用户问题：{input}

已完成步骤：
{past_steps}

剩余计划：
{remaining_plan}

规则：
1. 如果所有步骤已完成且能回答用户问题，输出: {{"response": "最终答案"}}
2. 如果还需要继续执行，输出: {{"plan": ["剩余步骤1", "剩余步骤2"]}}
3. 可根据已完成步骤的结果调整后续计划
4. 最终答案要简洁，使用中文，控制在 300 字以内"""


async def planner(state: PlanExecuteState) -> dict[str, Any]:
    """LLM 生成执行计划。"""
    llm = _build_llm()
    messages = [
        SystemMessage(content=PLANNER_PROMPT),
        HumanMessage(content=state["input"]),
    ]
    response = await llm.ainvoke(messages)
    try:
        parsed = _parse_json_from_response(response.content)
        steps = parsed.get("steps", [])
    except (json.JSONDecodeError, KeyError):
        steps = [f"用 search_company 搜索'{state['input']}'获取企业信息，然后用 assess_risk 评估风险"]
    return {"plan": steps, "past_steps": [], "response": None}


async def executor(state: PlanExecuteState) -> dict[str, Any]:
    """执行计划中的下一步。"""
    if not state["plan"]:
        return {"past_steps": state["past_steps"]}

    current_step = state["plan"][0]
    remaining_plan = state["plan"][1:]

    # 构建工具描述
    tools_desc = "\n".join(
        f"- {t.name}: {t.description.split(chr(10))[0]}" for t in TOOLS_LIST
    )
    executor_prompt = EXECUTOR_PROMPT.format(tools_desc=tools_desc)

    # 用 LLM 确定工具和参数
    llm = _build_llm()
    messages = [
        SystemMessage(content=executor_prompt),
        HumanMessage(content=f"步骤：{current_step}"),
    ]
    response = await llm.ainvoke(messages)

    try:
        parsed = _parse_json_from_response(response.content)
        tool_name = parsed["tool"]
        tool_args = parsed.get("args", {})
    except (json.JSONDecodeError, KeyError):
        result = f"工具调用解析失败: {response.content}"
        return {
            "plan": remaining_plan,
            "past_steps": state["past_steps"] + [(current_step, result)],
        }

    # 执行工具
    tool_fn = _TOOLS_MAP.get(tool_name)
    if tool_fn is None:
        result = f"未找到工具: {tool_name}"
    else:
        try:
            raw_result = await asyncio.to_thread(tool_fn.invoke, tool_args)
            result = json.dumps(raw_result, ensure_ascii=False, default=str)
        except Exception as e:
            result = f"工具执行错误: {str(e)}"

    return {
        "plan": remaining_plan,
        "past_steps": state["past_steps"] + [(current_step, result)],
    }


async def replanner(state: PlanExecuteState) -> dict[str, Any]:
    """根据已完成步骤决定是否继续执行。"""
    if not state["plan"]:
        past_steps_text = "\n".join(
            f"步骤: {step}\n结果: {result}" for step, result in state["past_steps"]
        )
        replan_prompt = REPLANNER_PROMPT.format(
            input=state["input"],
            past_steps=past_steps_text,
            remaining_plan="(无)",
        )

        llm = _build_llm()
        messages = [
            SystemMessage(content=replan_prompt),
            HumanMessage(content="所有步骤已完成，请生成最终答案。"),
        ]
        response = await llm.ainvoke(messages)
        try:
            parsed = _parse_json_from_response(response.content)
            final_response = parsed.get("response", response.content)
        except json.JSONDecodeError:
            final_response = response.content
        return {"response": final_response}

    # 还有剩余步骤，可能根据结果调整计划
    past_steps_text = "\n".join(
        f"步骤: {step}\n结果: {result}" for step, result in state["past_steps"]
    )
    remaining_text = "\n".join(state["plan"])
    replan_prompt = REPLANNER_PROMPT.format(
        input=state["input"],
        past_steps=past_steps_text,
        remaining_plan=remaining_text,
    )

    llm = _build_llm()
    messages = [
        SystemMessage(content=replan_prompt),
        HumanMessage(content="根据已完成步骤的结果，是否需要调整剩余计划？"),
    ]
    response = await llm.ainvoke(messages)

    try:
        parsed = _parse_json_from_response(response.content)
        if "response" in parsed:
            return {"response": parsed["response"]}
        new_plan = parsed.get("plan", state["plan"])
    except json.JSONDecodeError:
        new_plan = state["plan"]

    return {"plan": new_plan}


def should_end(state: PlanExecuteState) -> str:
    """判断是否结束执行。"""
    if state.get("response"):
        return END
    return "executor"


def build_plan_execute_graph():
    """构建并编译 Plan-Execute 图。"""
    graph = StateGraph(PlanExecuteState)

    graph.add_node("planner", planner)
    graph.add_node("executor", executor)
    graph.add_node("replanner", replanner)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "replanner")
    graph.add_conditional_edges("replanner", should_end, {"executor": "executor", END: END})

    return graph.compile()


def _sse_event(event_type: str, data: dict) -> str:
    """Format data as SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_plan_execute_graph(
    user_message: str,
    session_id: str,
    history: list[dict] | None = None,
):
    """运行 Plan-Execute 图并 yield SSE 事件。

    Events: thinking, plan, tool_call, tool_result, answer_chunk, done, error
    """
    graph = build_plan_execute_graph()
    full_answer = ""

    try:
        yield _sse_event("thinking", {"message": "正在分析问题并制定执行计划..."})

        async for event in graph.astream_events(
            {"input": user_message, "plan": [], "past_steps": [], "response": None},
            version="v2",
        ):
            kind = event.get("event", "")

            # 节点执行完成
            if kind == "on_chain_end":
                node_name = event.get("name", "")
                output = event.get("data", {}).get("output", {})

                # planner 节点完成 - 输出计划
                if node_name == "planner" and isinstance(output, dict):
                    plan = output.get("plan", [])
                    if plan:
                        yield _sse_event("plan", {"steps": plan})

                # executor 节点完成 - 输出工具调用结果
                elif node_name == "executor" and isinstance(output, dict):
                    past_steps = output.get("past_steps", [])
                    if past_steps:
                        last_step, last_result = past_steps[-1]
                        yield _sse_event("tool_result", {
                            "tool": last_step,
                            "result": last_result,
                        })

                # replanner 节点完成 - 可能有最终答案
                elif node_name == "replanner" and isinstance(output, dict):
                    resp = output.get("response")
                    if resp:
                        full_answer = resp

        # 输出最终答案
        if full_answer:
            for i in range(0, len(full_answer), 10):
                chunk = full_answer[i:i + 10]
                yield _sse_event("answer_chunk", {"text": chunk})
                await asyncio.sleep(0.02)

            from app.services.agent import _save_turn
            _save_turn(session_id, user_message, full_answer)

        yield _sse_event("done", {"answer": full_answer})

    except Exception as e:
        yield _sse_event("error", {"message": f"Plan-Execute 执行错误: {str(e)}"})
