"""
ReAct Agent with tool calling, conversation memory, and streaming support.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from openai import OpenAI

from app.db.mongo import get_db

MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
MAX_ITERATIONS = 12

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.getenv("LLM_API_KEY", ""),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        )
    return _client


# ---- tools ----

TOOLS: dict[str, tuple[callable, str]] = {}


def _register(name: str, desc: str):
    def decorator(fn):
        TOOLS[name] = (fn, desc)
        return fn
    return decorator


@_register("search_company", "搜索企业完整名称。输入：{\"keyword\": \"公司关键词\"}")
def _search(keyword: str) -> dict:
    from app.repositories.company_repo import search_companies
    from app.services.tianyancha_client import fetch_company

    results = search_companies(keyword)
    if not results:
        # try API fallback with the keyword as company name
        try:
            fetch_company(keyword)
        except Exception:
            pass
        results = search_companies(keyword)

    return {"keyword": keyword, "count": len(results), "results": results}


@_register("assess_risk", "评估供应商风险，返回风险评分、财报、风险明细。输入：{\"company_name\": \"完整名称\"}")
def _assess(company_name: str) -> dict:
    from app.schemas import RiskAssessRequest
    from app.services.risk_service import assess_risk
    result = assess_risk(RiskAssessRequest(company_name=company_name))
    return result.model_dump()


@_register("check_alert", "查看预警变化。输入：{\"company_name\": \"完整名称\"}")
def _check(company_name: str) -> dict:
    from app.services.alert_service import detect_changes, get_latest_snapshot
    if not get_latest_snapshot(company_name):
        return {"message": "暂无历史快照"}
    return detect_changes(company_name)


@_register("get_watchlist", "获取监控清单。无参数。")
def _list(**kwargs) -> dict:
    from app.services.alert_service import get_watchlist
    companies = get_watchlist()
    return {"count": len(companies), "companies": companies}


@_register("add_to_watchlist", "加入监控。输入：{\"company_name\": \"完整名称\"}")
def _watch(company_name: str) -> dict:
    from app.services.alert_service import add_to_watchlist
    return add_to_watchlist(company_name)


@_register("remove_from_watchlist", "移除监控。输入：{\"company_name\": \"完整名称\"}")
def _unwatch(company_name: str) -> dict:
    from app.services.alert_service import remove_from_watchlist
    return remove_from_watchlist(company_name)


@_register("esg_assessment", "评估企业ESG风险（环境/社会/治理三维评分）。输入：{\"company_name\": \"完整名称\"}")
def _esg(company_name: str) -> dict:
    from app.services.esg_service import assess_esg
    result = assess_esg(company_name)
    if result is None:
        return {"error": "未找到企业数据"}
    return result


@_register("contagion_analysis", "分析企业风险传染路径（分支机构/供应链/同行业）。输入：{\"company_name\": \"完整名称\"}")
def _contagion(company_name: str) -> dict:
    from app.services.contagion import analyze_contagion
    return analyze_contagion(company_name)


@_register("sentiment_analysis", "分析企业舆情情感（新闻搜索+LLM分析）。输入：{\"company_name\": \"完整名称\"}")
def _sentiment(company_name: str) -> dict:
    from app.services.sentiment import analyze_sentiment
    result = analyze_sentiment(company_name)
    if result is None:
        return {"error": "暂无舆情数据"}
    return {
        "company_name": result["company_name"],
        "sentiment_score": result["sentiment_score"],
        "negative_count": result["negative_count"],
        "neutral_count": result["neutral_count"],
        "positive_count": result["positive_count"],
        "articles_count": result["articles_count"],
        "summary": result["summary"],
        "key_concerns": result.get("key_concerns", []),
        "risk_tags": [t["tag"] for t in result.get("risk_tags", [])],
    }


@_register("predict_risk", "预测企业未来6-12月风险恶化概率。输入：{\"company_name\": \"完整名称\"}")
def _predict(company_name: str) -> dict:
    from app.services.predictor import predict_company
    result = predict_company(company_name)
    if result is None:
        return {"error": "未找到企业数据"}
    return result


@_register("macro_risk", "分析企业宏观风险（行业PMI景气+地区信用+政策标签）。输入：{\"company_name\": \"完整名称\"}")
def _macro(company_name: str) -> dict:
    from app.services.macro_service import assess_macro_risk
    return assess_macro_risk(company_name)


@_register("find_alternatives", "为高风险企业推荐同行业低风险替代供应商。输入：{\"company_name\": \"完整名称\"}")
def _alternatives(company_name: str) -> dict:
    from app.services.alternative_service import find_alternatives
    return find_alternatives(company_name)


@_register("scenario_simulate", "模拟供应商倒闭/诉讼等情景下的影响。输入：{\"company_name\": \"完整名称\", \"scenario\": \"bankruptcy|lawsuit|disruption|quality\"}")
def _simulate(company_name: str, scenario: str = "bankruptcy") -> dict:
    from app.services.scenario_service import simulate
    return simulate(company_name, scenario)


@_register("check_sanctions", "筛查企业是否在国际制裁/黑名单中（OFAC实体清单/失信等）。输入：{\"company_name\": \"完整名称\"}")
def _sanctions(company_name: str) -> dict:
    from app.services.sanctions_service import check_sanctions
    return check_sanctions(company_name)


@_register("knowledge_search", "从知识库检索相关文档（财报、合同、ESG报告等）。输入：{\"query\": \"搜索关键词\", \"company_name\": \"可选，企业全称\"}")
def _knowledge_search(query: str, company_name: str = "") -> dict:
    from app.services.retriever import retrieve_context
    context = retrieve_context(query, n_results=5)
    if not context:
        return {"message": "未找到相关文档", "suggestion": "请先上传相关文档到知识库"}
    return {
        "query": query,
        "company_name": company_name,
        "context": context,
        "message": "以下是从知识库检索到的相关信息",
    }


# ---- history ----

def _load_history(session_id: str) -> list[dict]:
    db = get_db()
    doc = db["conversations"].find_one({"session_id": session_id})
    if not doc:
        return []
    # strip extra fields, keep only role + content
    return [{"role": m["role"], "content": m["content"]} for m in doc["messages"]]


def _save_turn(session_id: str, user_msg: str, assistant_msg: str) -> None:
    db = get_db()
    now = datetime.now(timezone.utc)
    db["conversations"].update_one(
        {"session_id": session_id},
        {
            "$push": {
                "messages": {
                    "$each": [
                        {"role": "user", "content": user_msg},
                        {"role": "assistant", "content": assistant_msg},
                    ]
                }
            },
            "$setOnInsert": {"session_id": session_id, "created_at": now},
        },
        upsert=True,
    )


# ---- ReAct loop ----

SYSTEM_PROMPT = """你是采购风险分析专家。

可用工具：
{tool_descriptions}

**你必须先用工具获取数据，绝不能在没有数据的情况下直接回答。** 涉及查询、评估、分析的问题，第一步永远是调工具。

规则：
- 调工具：{{"tool": "工具名", "args": {{"参数": "值"}}}}
- 回答：{{"answer": "回复内容"}}
- 每次只调一个工具
- **严禁编造任何数据**，所有数字必须来自工具返回结果
- 看清单：用 get_watchlist
- 看风险：先用 search_company 搜全名，再用 assess_risk
- 看ESG：用 esg_assessment 获取环境/社会/治理三维评分
- 看传染：用 contagion_analysis 查关联方和供应链风险
- 看舆情：用 sentiment_analysis 看新闻情感趋势
- 看预测：用 predict_risk 看未来风险恶化概率
- 看宏观：用 macro_risk 看行业景气+地区风险+政策标签
- 找替代：用 find_alternatives 为高风险企业推荐同行业低风险供应商
- 情景模拟：用 scenario_simulate 模拟供应商倒闭/诉讼等影响
- 制裁筛查：用 check_sanctions 查国际制裁/失信/黑名单
- 知识库检索：用 knowledge_search 检索上传的文档（财报、合同、ESG报告等）
- 要对比多家：先 get_watchlist，再逐个 assess_risk
- assess_risk 已含财报数据，上市公司要分析财报
- debt_ratio=0 表示数据缺失（港股），不要解读为低负债
- in_watchlist=true 表示已在监控，不要建议"加入监控"
- 综合问题可调多个工具（ESG+风险+舆情），但每次只调一个
- 搜不到就告知用户，300字以内"""


def _build_tool_prompt() -> str:
    lines = []
    for name, (_, desc) in TOOLS.items():
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)


def chat(session_id: str, message: str) -> str:
    history = _load_history(session_id)
    system = SYSTEM_PROMPT.replace("{tool_descriptions}", _build_tool_prompt())

    messages = [{"role": "system", "content": system}]
    for m in history[-20:]:  # last 10 turns
        messages.append(m)
    messages.append({"role": "user", "content": message})

    tool_called = False

    for _ in range(MAX_ITERATIONS):
        resp = _get_client().chat.completions.create(
            model=MODEL, messages=messages, temperature=0,
        )
        text = resp.choices[0].message.content.strip() or ""

        parsed = _try_parse(text)
        if parsed is None:
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": "请严格按JSON格式回复：{\"tool\": \"工具名\", \"args\": {...}} 或 {\"answer\": \"...\"}"})
            continue

        if "tool" in parsed:
            tool_called = True
            tool_name = parsed["tool"]
            args = parsed.get("args", {})
            if tool_name not in TOOLS:
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": f"未知工具 {tool_name}。可用工具：{list(TOOLS.keys())}"})
                continue

            fn = TOOLS[tool_name][0]
            try:
                result = fn(**args)
            except Exception as e:
                result = {"error": str(e)}

            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": f"工具返回：{json.dumps(result, ensure_ascii=False)}"})
            continue

        if "answer" in parsed:
            # require at least one tool call for analysis questions
            if not tool_called and any(kw in message for kw in ["风险", "评估", "分析", "财务", "诉讼", "监控", "查", "看", "对比"]):
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": "请先用工具获取真实数据，不要直接编造内容。"})
                continue

            answer = parsed["answer"]
            _save_turn(session_id, message, answer)
            return answer

    # fallback: force answer from last content
    fallback = _force_answer(messages)
    _save_turn(session_id, message, fallback)
    return fallback


def _try_parse(text: str) -> dict | None:
    # extract JSON from text (handle markdown code blocks)
    for part in text.split("```"):
        part = part.strip()
        if part.startswith("json"):
            part = part[4:]
        try:
            result = json.loads(part)
            if "tool" in result or "answer" in result:
                return result
        except json.JSONDecodeError:
            continue

    # try the raw text itself
    try:
        result = json.loads(text)
        if "tool" in result or "answer" in result:
            return result
    except json.JSONDecodeError:
        pass

    # try to extract JSON object from text (handle cases where LLM adds extra text)
    import re
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        try:
            result = json.loads(json_match.group())
            if "tool" in result or "answer" in result:
                return result
        except json.JSONDecodeError:
            pass

    return None


def _force_answer(messages: list) -> str:
    messages.append({"role": "user", "content": "你已达到最大调用次数。请基于已有数据直接回答。"})
    resp = _get_client().chat.completions.create(
        model=MODEL, messages=messages,
    )
    return resp.choices[0].message.content or "抱歉，分析超时，请重试。"


# ---- Streaming ----

async def chat_stream(session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    Async generator that yields SSE events for streaming response.
    Events: thinking, tool_call, tool_result, answer_chunk, done, error
    """
    history = _load_history(session_id)
    system = SYSTEM_PROMPT.replace("{tool_descriptions}", _build_tool_prompt())

    messages = [{"role": "system", "content": system}]
    for m in history[-20:]:
        messages.append(m)
    messages.append({"role": "user", "content": message})

    tool_called = False
    full_answer = ""

    try:
        for iteration in range(MAX_ITERATIONS):
            yield _sse_event("thinking", {"iteration": iteration + 1, "message": f"思考中... (第 {iteration + 1} 轮)"})

            # Call LLM in thread pool to avoid blocking
            resp = await asyncio.to_thread(
                _get_client().chat.completions.create,
                model=MODEL, messages=messages, temperature=0,
            )
            text = resp.choices[0].message.content.strip() or ""

            parsed = _try_parse(text)
            if parsed is None:
                messages.append({"role": "assistant", "content": text})
                yield _sse_event("error", {"message": "格式错误，正在重试..."})
                messages.append({"role": "user", "content": "请严格按JSON格式回复：{\"tool\": \"工具名\", \"args\": {...}} 或 {\"answer\": \"...\"}"})
                continue

            if "tool" in parsed:
                tool_called = True
                tool_name = parsed["tool"]
                args = parsed.get("args", {})

                if tool_name not in TOOLS:
                    messages.append({"role": "assistant", "content": text})
                    yield _sse_event("error", {"message": f"未知工具: {tool_name}"})
                    messages.append({"role": "user", "content": f"未知工具 {tool_name}。可用工具：{list(TOOLS.keys())}"})
                    continue

                yield _sse_event("tool_call", {"tool": tool_name, "args": args})

                fn = TOOLS[tool_name][0]
                try:
                    result = await asyncio.to_thread(fn, **args)
                except Exception as e:
                    result = {"error": str(e)}

                yield _sse_event("tool_result", {"tool": tool_name, "result": result})

                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": f"工具返回：{json.dumps(result, ensure_ascii=False)}"})
                continue

            if "answer" in parsed:
                # require at least one tool call for analysis questions
                if not tool_called and any(kw in message for kw in ["风险", "评估", "分析", "财务", "诉讼", "监控", "查", "看", "对比"]):
                    messages.append({"role": "assistant", "content": text})
                    yield _sse_event("thinking", {"message": "需要先获取数据..."})
                    messages.append({"role": "user", "content": "请先用工具获取真实数据，不要直接编造内容。"})
                    continue

                answer = parsed["answer"]
                # Stream answer chunk by chunk (simulate typing)
                for i in range(0, len(answer), 10):
                    chunk = answer[i:i+10]
                    full_answer += chunk
                    yield _sse_event("answer_chunk", {"text": chunk})
                    await asyncio.sleep(0.02)  # Small delay for typing effect

                _save_turn(session_id, message, answer)
                yield _sse_event("done", {"answer": answer})
                return

        # Max iterations reached
        yield _sse_event("thinking", {"message": "达到最大调用次数，正在生成最终回答..."})
        fallback = await asyncio.to_thread(_force_answer, messages)
        _save_turn(session_id, message, fallback)
        yield _sse_event("answer_chunk", {"text": fallback})
        yield _sse_event("done", {"answer": fallback})

    except Exception as e:
        yield _sse_event("error", {"message": f"发生错误: {str(e)}"})


def _sse_event(event_type: str, data: dict) -> str:
    """Format data as SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def chat_stream_with_plan(session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    Plan-and-Execute mode with streaming.
    Uses planner to generate execution plan, then executor to run it.
    """
    from app.services.executor import execute_plan_stream
    from app.db.mongo import get_db

    try:
        # Execute plan with streaming
        async for event in execute_plan_stream(message, session_id):
            event_type = event.get("type", "")

            if event_type == "planning":
                yield _sse_event("thinking", {"message": event.get("thought", "")})
            elif event_type == "plan":
                yield _sse_event("plan", {"steps": event.get("steps", [])})
            elif event_type == "parallel_start":
                yield _sse_event("thinking", {"message": f"并行执行 {event.get('count', 0)} 个任务..."})
            elif event_type == "step_start":
                yield _sse_event("tool_call", {
                    "tool": event.get("tool", ""),
                    "args": event.get("args", {}),
                })
            elif event_type == "step_result":
                yield _sse_event("tool_result", {
                    "tool": event.get("tool", ""),
                    "result": event.get("result", {}),
                })
            elif event_type == "step_error":
                yield _sse_event("error", {
                    "message": f"{event.get('tool', '')}: {event.get('error', '')}",
                })
            elif event_type == "thinking":
                yield _sse_event("thinking", {"message": event.get("message", "")})
            elif event_type == "final_answer":
                answer = event.get("answer", "")
                # Save to conversation history
                db = get_db()
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                db["conversations"].update_one(
                    {"session_id": session_id},
                    {
                        "$push": {
                            "messages": {
                                "$each": [
                                    {"role": "user", "content": message},
                                    {"role": "assistant", "content": answer},
                                ]
                            }
                        },
                        "$setOnInsert": {"session_id": session_id, "created_at": now},
                    },
                    upsert=True,
                )
                # Stream answer in chunks
                for i in range(0, len(answer), 10):
                    chunk = answer[i:i+10]
                    yield _sse_event("answer_chunk", {"text": chunk})
                    await asyncio.sleep(0.02)
                yield _sse_event("done", {"answer": answer})

    except Exception as e:
        yield _sse_event("error", {"message": f"执行错误: {str(e)}"})


async def chat_stream_with_agents(session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    Multi-Agent mode with streaming.
    Uses coordinator to route query to specialized agents.
    """
    from app.agents.coordinator import coordinator
    from app.db.mongo import get_db

    try:
        # Extract company name from message (simple heuristic)
        context = {}
        # Try to find company name in message
        for keyword in ["公司", "企业", "供应商"]:
            if keyword in message:
                # Extract words around keyword
                idx = message.index(keyword)
                start = max(0, idx - 10)
                end = min(len(message), idx + 20)
                context["company_name"] = message[start:end].strip()
                break

        # Use coordinator to analyze
        async for event in coordinator.analyze_with_stream(message, context):
            event_type = event.get("type", "")

            if event_type == "thinking":
                yield _sse_event("thinking", {"message": event.get("message", "")})
            elif event_type == "agent_selection":
                yield _sse_event("agent_selection", {
                    "agents": event.get("agents", []),
                    "reasoning": event.get("reasoning", ""),
                })
            elif event_type == "agent_start":
                yield _sse_event("agent_start", {
                    "agent": event.get("agent", ""),
                    "description": event.get("description", ""),
                })
            elif event_type == "agent_complete":
                yield _sse_event("agent_complete", {
                    "agent": event.get("agent", ""),
                    "summary": event.get("result_summary", ""),
                })
            elif event_type == "agent_error":
                yield _sse_event("error", {
                    "message": f"{event.get('agent', '')}: {event.get('error', '')}",
                })
            elif event_type == "final_answer":
                answer = event.get("answer", "")
                # Save to conversation history
                db = get_db()
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                db["conversations"].update_one(
                    {"session_id": session_id},
                    {
                        "$push": {
                            "messages": {
                                "$each": [
                                    {"role": "user", "content": message},
                                    {"role": "assistant", "content": answer},
                                ]
                            }
                        },
                        "$setOnInsert": {"session_id": session_id, "created_at": now},
                    },
                    upsert=True,
                )
                # Stream answer in chunks
                for i in range(0, len(answer), 10):
                    chunk = answer[i:i+10]
                    yield _sse_event("answer_chunk", {"text": chunk})
                    await asyncio.sleep(0.02)
                yield _sse_event("done", {"answer": answer})

    except Exception as e:
        yield _sse_event("error", {"message": f"Multi-Agent 执行错误: {str(e)}"})
