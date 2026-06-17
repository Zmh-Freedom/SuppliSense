"""LangGraph Supervisor Graph — 多智能体协作编排。

使用 supervisor 模式替代手写 coordinator.py，通过 LLM 路由到
risk / sentiment / compliance 三个子 Agent，循环执行直到完成。
"""

import json
import os
from typing import Annotated, Any, AsyncGenerator, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

SUPERVISOR_SYSTEM_PROMPT = (
    "你是任务分配专家。根据用户查询，选择最合适的分析 Agent。\n"
    "可用 Agent：risk（风险评估）、sentiment（舆情分析）、compliance（合规检查）。\n"
    '输出 JSON：{"next": "agent_name"} 或 {"next": "FINISH"}。\n'
    "简单问候返回 FINISH。"
)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class SupervisorState(TypedDict):
    """Supervisor graph 的共享状态。"""

    messages: Annotated[list, add_messages]
    next: str  # "risk" | "sentiment" | "compliance" | "FINISH"


# ---------------------------------------------------------------------------
# LLM builder
# ---------------------------------------------------------------------------

def _build_supervisor_llm() -> ChatOpenAI:
    """构建 supervisor 使用的 LLM（不绑定工具）。"""
    return ChatOpenAI(
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        api_key=os.getenv("LLM_API_KEY", ""),
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        temperature=0,
    )


# ---------------------------------------------------------------------------
# Supervisor node
# ---------------------------------------------------------------------------

async def supervisor_node(state: SupervisorState) -> dict[str, Any]:
    """LLM 路由节点：决定下一步调用哪个 Agent 或结束。"""
    llm = _build_supervisor_llm()

    messages = [SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT)] + list(
        state["messages"]
    )

    response: AIMessage = await llm.ainvoke(messages)
    text = response.content.strip()

    next_target = _parse_next(text)

    return {"next": next_target}


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

    # 兜底：如果文本中包含已知关键词
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
# Agent node functions (lazy import sub-graphs)
# ---------------------------------------------------------------------------

async def risk_node(state: SupervisorState) -> dict[str, Any]:
    """调用 risk 子 Agent 图。"""
    from app.graphs.agents.risk_agent import build_risk_agent

    graph = build_risk_agent()
    result = await graph.ainvoke({"messages": state["messages"]})
    last_msg = result["messages"][-1]
    return {"messages": [last_msg]}


async def sentiment_node(state: SupervisorState) -> dict[str, Any]:
    """调用 sentiment 子 Agent 图。"""
    from app.graphs.agents.sentiment_agent import build_sentiment_agent

    graph = build_sentiment_agent()
    result = await graph.ainvoke({"messages": state["messages"]})
    last_msg = result["messages"][-1]
    return {"messages": [last_msg]}


async def compliance_node(state: SupervisorState) -> dict[str, Any]:
    """调用 compliance 子 Agent 图。"""
    from app.graphs.agents.compliance_agent import build_compliance_agent

    graph = build_compliance_agent()
    result = await graph.ainvoke({"messages": state["messages"]})
    last_msg = result["messages"][-1]
    return {"messages": [last_msg]}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route_supervisor(state: SupervisorState) -> str:
    """根据 supervisor 的 next 字段决定下一个节点。"""
    nxt = state.get("next", "FINISH")
    if nxt == "risk":
        return "risk"
    if nxt == "sentiment":
        return "sentiment"
    if nxt == "compliance":
        return "compliance"
    return END


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_supervisor_graph():
    """构建并编译 supervisor 图。

    流程：
        supervisor → (risk | sentiment | compliance | END)
        risk → supervisor
        sentiment → supervisor
        compliance → supervisor
    """
    graph = StateGraph(SupervisorState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("risk", risk_node)
    graph.add_node("sentiment", sentiment_node)
    graph.add_node("compliance", compliance_node)

    graph.set_entry_point("supervisor")

    graph.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {
            "risk": "risk",
            "sentiment": "sentiment",
            "compliance": "compliance",
            END: END,
        },
    )

    graph.add_edge("risk", "supervisor")
    graph.add_edge("sentiment", "supervisor")
    graph.add_edge("compliance", "supervisor")

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
) -> AsyncGenerator[str, None]:
    """运行 supervisor 图并 yield SSE 事件。

    Events: thinking, agent_selection, agent_start, agent_complete,
            answer_chunk, done, error
    """
    from app.services.agent import _save_turn

    input_messages: list = []
    if history:
        for m in history:
            input_messages.append({"role": m["role"], "content": m["content"]})
    input_messages.append(HumanMessage(content=user_message))

    graph = build_supervisor_graph()
    full_answer = ""
    current_agent: str | None = None

    try:
        async for event in graph.astream_events(
            {"messages": input_messages, "next": ""},
            version="v2",
        ):
            kind = event.get("event", "")

            # --- supervisor 节点产出 next ---
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

            # --- 子 Agent 节点开始 ---
            elif kind == "on_chain_start" and event.get("name") in (
                "risk",
                "sentiment",
                "compliance",
            ):
                agent_name = event["name"]
                current_agent = agent_name
                yield _sse_event("agent_start", {"agent": agent_name})

            # --- 子 Agent 节点完成 ---
            elif kind == "on_chain_end" and event.get("name") in (
                "risk",
                "sentiment",
                "compliance",
            ):
                agent_name = event["name"]
                output = event.get("data", {}).get("output", {})
                if isinstance(output, dict) and "messages" in output:
                    last = output["messages"][-1]
                    content = last.content if hasattr(last, "content") else str(last)
                    full_answer = content
                yield _sse_event("agent_complete", {"agent": agent_name})

            # --- LLM 流式 token（子 Agent 内部）---
            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and chunk.content:
                    full_answer += chunk.content
                    yield _sse_event("answer_chunk", {"text": chunk.content})

            # --- LLM 完整响应（非流式时兜底）---
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
