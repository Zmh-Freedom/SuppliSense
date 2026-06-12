"""
Plan-and-Execute: Task Planner
LLM analyzes user request and generates a step-by-step execution plan.
"""

import asyncio
import json
import os
from typing import Any

from openai import OpenAI

from app.services.agent import TOOLS, _get_client, MODEL


PLANNER_PROMPT = """你是一个任务规划专家。分析用户请求，生成执行计划。

可用工具：
{tool_descriptions}

**规划规则**：
1. 每个步骤必须使用上述工具之一
2. 如果多个步骤之间没有依赖关系，标记 parallel: true
3. 步骤之间如果有依赖（如后续步骤需要前面的结果），标记 parallel: false
4. 尽量合并可以并行的任务

**输出格式**（严格 JSON）：
```json
{{
  "thought": "分析思路",
  "steps": [
    {{"tool": "工具名", "args": {{"参数": "值"}}, "parallel": false}},
    {{"tool": "工具名", "args": {{"参数": "值"}}, "parallel": true}}
  ]
}}
```

**示例**：
用户："对比海康威视和大华股份的风险"
```json
{{
  "thought": "需要先搜索两家公司获取全名，然后并行评估风险",
  "steps": [
    {{"tool": "search_company", "args": {{"keyword": "海康威视"}}, "parallel": false}},
    {{"tool": "search_company", "args": {{"keyword": "大华股份"}}, "parallel": false}},
    {{"tool": "assess_risk", "args": {{"company_name": "杭州海康威视数字技术股份有限公司"}}, "parallel": true}},
    {{"tool": "assess_risk", "args": {{"company_name": "浙江大华技术股份有限公司"}}, "parallel": true}}
  ]
}}
```

用户："分析监控清单里所有企业的风险"
```json
{{
  "thought": "先获取监控清单，然后并行评估每个企业",
  "steps": [
    {{"tool": "get_watchlist", "args": {{}}, "parallel": false}}
  ]
}}
```
注意：get_watchlist 返回清单后，需要根据结果动态添加后续的 assess_risk 步骤。

用户："简单问题，如'你好'"
```json
{{
  "thought": "简单问候，不需要工具",
  "steps": []
}}
```

**重要**：
- 如果问题简单（问候、闲聊），steps 可以为空
- 复杂问题才需要规划
- 每次只规划第一步需要的工具，后续步骤需要根据前面的结果动态调整
"""


def _build_tool_descriptions() -> str:
    lines = []
    for name, (_, desc) in TOOLS.items():
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)


async def plan_task(user_message: str, context: str = "") -> dict[str, Any]:
    """
    Analyze user request and generate execution plan.

    Returns:
        {
            "thought": "分析思路",
            "steps": [{"tool": "...", "args": {...}, "parallel": bool}, ...]
        }
    """
    system = PLANNER_PROMPT.replace("{tool_descriptions}", _build_tool_descriptions())

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]

    if context:
        messages.insert(1, {"role": "user", "content": f"上下文：{context}"})

    # Call LLM in thread pool
    resp = await asyncio.to_thread(
        _get_client().chat.completions.create,
        model=MODEL,
        messages=messages,
        temperature=0,
    )

    text = resp.choices[0].message.content.strip() or ""

    # Parse JSON from response
    plan = _parse_plan(text)
    if plan is None:
        # Fallback: return empty plan
        return {
            "thought": "无法解析计划，使用默认行为",
            "steps": [],
            "raw_response": text,
        }

    return plan


def _parse_plan(text: str) -> dict[str, Any] | None:
    """Extract plan JSON from LLM response."""
    # Try to extract from markdown code blocks
    for part in text.split("```"):
        part = part.strip()
        if part.startswith("json"):
            part = part[4:]
        try:
            result = json.loads(part)
            if "steps" in result:
                return result
        except json.JSONDecodeError:
            continue

    # Try the whole text
    try:
        result = json.loads(text)
        if "steps" in result:
            return result
    except json.JSONDecodeError:
        pass

    return None
