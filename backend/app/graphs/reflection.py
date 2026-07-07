"""Self-Reflection 模块 — 可复用的 Agent 输出质量审查节点。

在任意 LangGraph 图中插入 reflection 步骤：
1. agent 产生 final answer（无 tool_calls）→ 路由到 reflector
2. reflector 审查输出质量（幻觉、一致性、完整性）
3. 发现问题 → 反馈给 agent 重新生成（最多 1 次纠正循环）
4. 通过或达到最大次数 → END
"""

import json
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END


REFLECTOR_SYSTEM_PROMPT = """你是一个严谨的分析审核专家。请审查以下 Agent 的输出质量：

审查维度：
1. **数据幻觉检查**：分析中引用的具体数字、百分比、风险评分是否都能从工具调用结果中找到依据？编造的数据标记为幻觉。
2. **一致性检查**：风险评分和风险等级描述是否一致？（score > 60 对应"高风险"，30-60 对应"中风险"，<30 对应"低风险"）
3. **完整性检查**：用户要求的所有分析维度（风险、舆情、合规、ESG 等）是否都已覆盖？

输出 JSON（只输出 JSON，不要其他内容）：
- 通过审查：{"pass": true, "feedback": ""}
- 发现问题：{"pass": false, "feedback": "具体问题描述和修改建议（中文，100字以内）"}

工具调用记录：
{tool_results}

Agent 输出：
{agent_output}

用户原始问题：
{user_query}"""


def _json_candidates(text: str) -> list[str]:
    """从文本中提取可能的 JSON 片段。"""
    candidates: list[str] = [text]
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:]
            candidates.append(part.strip())
    return candidates


def _parse_reflection(text: str) -> dict[str, Any]:
    """从 LLM 回复中解析 reflection 结果。"""
    for candidate in _json_candidates(text):
        try:
            obj = json.loads(candidate)
            if "pass" in obj:
                return {
                    "pass": bool(obj.get("pass", True)),
                    "feedback": str(obj.get("feedback", "")),
                }
        except json.JSONDecodeError:
            continue

    # 解析失败，默认通过
    return {"pass": True, "feedback": ""}


def build_reflector_node() -> Callable:
    """构建 reflector 节点函数。

    返回一个 async callable，可作为 StateGraph.add_node() 的 action。
    要求 state 包含以下字段（调用方 graph 的 TypedDict 中声明）：
    - messages: list[BaseMessage] （含 add_messages reducer）
    - reflection_feedback: str
    - reflection_count: int
    """

    async def reflector(state: dict) -> dict[str, Any]:
        from app.graphs import build_shared_llm

        # 提取工具调用结果
        tool_lines: list[str] = []
        for m in state.get("messages", []):
            if hasattr(m, "tool_calls") and m.tool_calls:
                # AIMessage with tool_calls
                for tc in m.tool_calls:
                    tool_lines.append(
                        f"[{tc.get('name', 'unknown')}] args: {json.dumps(tc.get('args', {}), ensure_ascii=False)}"
                    )
            if getattr(m, "type", "") == "tool":
                # ToolMessage — 截断过长的结果
                content = str(m.content) if hasattr(m, "content") else str(m)
                tool_lines.append(f"result: {content[:1500]}")

        tool_results = "\n".join(tool_lines) if tool_lines else "(无工具调用)"

        # 提取 agent 的最后一次回答（最后一条 AIMessage）
        messages = state.get("messages", [])
        agent_output = ""
        for m in reversed(messages):
            if getattr(m, "type", "") == "ai" and hasattr(m, "content") and m.content:
                agent_output = str(m.content)
                break

        # 提取用户原始问题（第一条 HumanMessage）
        user_query = ""
        for m in messages:
            if isinstance(m, HumanMessage):
                user_query = str(m.content)
                break

        prompt = REFLECTOR_SYSTEM_PROMPT.format(
            tool_results=tool_results[:4000],
            agent_output=agent_output[:3000],
            user_query=user_query,
        )

        llm = build_shared_llm()
        response = await llm.ainvoke([SystemMessage(content=prompt)])

        result = _parse_reflection(str(response.content))
        new_count = state.get("reflection_count", 0) + 1

        return {
            "reflection_count": new_count,
            "reflection_feedback": "" if result["pass"] else result["feedback"],
        }

    return reflector


def route_after_reflector(state: dict) -> str:
    """反射后的条件路由。

    返回 "agent" → 回到 agent 节点纠正（最多 1 次）
    返回 END → 通过审查或已达最大纠正次数

    注意：调用方需在 add_conditional_edges 中将 "agent" 映射到实际的 agent 节点名。
    """
    feedback = state.get("reflection_feedback", "")
    count = state.get("reflection_count", 0)

    # 允许恰好 1 次纠正循环（count==1 表示第一次 reflection 完成）
    if feedback and count == 1:
        return "agent"

    return END
