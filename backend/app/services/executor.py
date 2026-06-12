"""
Plan-and-Execute: Task Executor
Executes plan steps, supporting parallel execution where applicable.
"""

import asyncio
import json
from typing import Any, AsyncGenerator

from app.services.agent import TOOLS
from app.services.planner import plan_task


async def execute_plan_stream(
    user_message: str,
    session_id: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Execute plan with streaming output.

    Yields events:
        - {"type": "planning", "thought": "..."}
        - {"type": "plan", "steps": [...]}
        - {"type": "step_start", "index": int, "tool": "...", "args": {...}}
        - {"type": "step_result", "index": int, "tool": "...", "result": {...}}
        - {"type": "step_error", "index": int, "tool": "...", "error": "..."}
        - {"type": "final_answer", "answer": "..."}
    """
    # Step 1: Generate plan
    yield {"type": "planning", "thought": "正在分析任务..."}

    plan = await plan_task(user_message)
    thought = plan.get("thought", "")
    steps = plan.get("steps", [])

    yield {"type": "planning", "thought": thought}
    yield {"type": "plan", "steps": steps}

    if not steps:
        # No steps planned, generate simple response
        yield {"type": "thinking", "message": "无需工具调用，生成回答..."}
        answer = await _generate_simple_answer(user_message)
        yield {"type": "final_answer", "answer": answer}
        return

    # Step 2: Execute steps
    results: list[dict[str, Any]] = []

    # Group steps by parallel execution
    i = 0
    while i < len(steps):
        step = steps[i]

        # Collect consecutive parallel steps
        parallel_group = [step]
        if step.get("parallel"):
            while i + 1 < len(steps) and steps[i + 1].get("parallel"):
                i += 1
                parallel_group.append(steps[i])

        # Execute parallel group
        if len(parallel_group) > 1:
            # Execute in parallel
            yield {"type": "parallel_start", "count": len(parallel_group)}

            tasks = []
            for j, s in enumerate(parallel_group):
                task = _execute_single_step(i - len(parallel_group) + j + 1, s)
                tasks.append(task)

            parallel_results = await asyncio.gather(*tasks, return_exceptions=True)

            for event in _format_parallel_results(parallel_group, parallel_results):
                yield event

            results.extend([r for r in parallel_results if not isinstance(r, Exception)])
        else:
            # Execute single step
            async for event in _execute_single_step_stream(i, step):
                yield event
                if event["type"] == "step_result":
                    results.append(event["result"])

        i += 1

    # Step 3: Generate final answer based on results
    yield {"type": "thinking", "message": "整理分析结果..."}
    answer = await _generate_final_answer(user_message, results, steps)
    yield {"type": "final_answer", "answer": answer}


async def _execute_single_step_stream(
    index: int,
    step: dict[str, Any],
) -> AsyncGenerator[dict[str, Any], None]:
    """Execute a single step with streaming output."""
    tool_name = step["tool"]
    args = step.get("args", {})

    yield {"type": "step_start", "index": index, "tool": tool_name, "args": args}

    if tool_name not in TOOLS:
        yield {"type": "step_error", "index": index, "tool": tool_name, "error": f"未知工具: {tool_name}"}
        return

    fn = TOOLS[tool_name][0]
    try:
        result = await asyncio.to_thread(fn, **args)
        yield {"type": "step_result", "index": index, "tool": tool_name, "result": result}
    except Exception as e:
        yield {"type": "step_error", "index": index, "tool": tool_name, "error": str(e)}


async def _execute_single_step(
    index: int,
    step: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single step and return result."""
    tool_name = step["tool"]
    args = step.get("args", {})

    if tool_name not in TOOLS:
        return {"tool": tool_name, "error": f"未知工具: {tool_name}"}

    fn = TOOLS[tool_name][0]
    try:
        result = await asyncio.to_thread(fn, **args)
        return {"tool": tool_name, "args": args, "result": result}
    except Exception as e:
        return {"tool": tool_name, "args": args, "error": str(e)}


def _format_parallel_results(
    steps: list[dict[str, Any]],
    results: list[Any],
) -> list[dict[str, Any]]:
    """Format parallel execution results as events."""
    events = []
    for i, (step, result) in enumerate(zip(steps, results)):
        tool_name = step["tool"]
        if isinstance(result, Exception):
            events.append({"type": "step_error", "index": i, "tool": tool_name, "error": str(result)})
        else:
            events.append({"type": "step_result", "index": i, "tool": tool_name, "result": result})
    return events


async def _generate_simple_answer(user_message: str) -> str:
    """Generate a simple answer without tool calls."""
    from app.services.agent import _get_client, MODEL, _save_turn

    messages = [
        {"role": "system", "content": "你是采购风险分析专家。请简洁、专业地回答用户问题。"},
        {"role": "user", "content": user_message},
    ]

    resp = await asyncio.to_thread(
        _get_client().chat.completions.create,
        model=MODEL,
        messages=messages,
        temperature=0.7,
    )

    return resp.choices[0].message.content or "抱歉，无法生成回答。"


async def _generate_final_answer(
    user_message: str,
    results: list[dict[str, Any]],
    steps: list[dict[str, Any]],
) -> str:
    """Generate final answer based on execution results."""
    from app.services.agent import _get_client, MODEL

    # Format results for LLM
    results_text = ""
    for i, (step, result) in enumerate(zip(steps, results)):
        tool_name = step.get("tool", "unknown")
        results_text += f"\n\n工具 {tool_name} 返回：\n{json.dumps(result, ensure_ascii=False, indent=2)}"

    system_prompt = f"""你是采购风险分析专家。基于以下工具调用结果，回答用户问题。

规则：
- 用清晰的中文总结关键发现
- 如有风险点，明确指出
- 给出专业建议
- 不要编造数据，所有信息必须来自工具结果
- 300字以内

用户问题：{user_message}

工具调用结果：
{results_text}
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    resp = await asyncio.to_thread(
        _get_client().chat.completions.create,
        model=MODEL,
        messages=messages,
        temperature=0.3,
    )

    return resp.choices[0].message.content or "抱歉，分析完成但无法生成总结。"
