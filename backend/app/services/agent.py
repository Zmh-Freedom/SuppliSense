"""
ReAct Agent with tool calling and conversation memory.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

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
- 涉及风险、财报、公司信息的问题，必须调工具获取数据后再回答
- 看清单：用 get_watchlist，不传参数写 {{}}
- 看风险：先用 search_company 搜全名，再用 assess_risk
- assess_risk 已包含财报数据（financial字段），上市公司要分析财报
- financial 中 debt_ratio=0 表示数据缺失（港股无此数据），不要解读为低负债
- 搜不到就告知用户，不要反复搜，300字以内"""


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

    return None


def _force_answer(messages: list) -> str:
    messages.append({"role": "user", "content": "你已达到最大调用次数。请基于已有数据直接回答。"})
    resp = _get_client().chat.completions.create(
        model=MODEL, messages=messages,
    )
    return resp.choices[0].message.content or "抱歉，分析超时，请重试。"
