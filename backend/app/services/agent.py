"""
ReAct Agent with tool calling, conversation memory, and streaming support.
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable

from openai import OpenAI

from app.core.config import settings
from app.db.mongo import get_db

MODEL = settings.LLM_MODEL
MAX_ITERATIONS = 12

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
        )
    return _client


# ---- tools ----


@dataclass
class ToolConfig:
    callable: Callable[..., Any]
    description: str
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    max_result_tokens: int = 2000


TOOLS: dict[str, ToolConfig] = {}


def _register(name: str, desc: str, parameters: dict | None = None):
    def decorator(fn):
        TOOLS[name] = ToolConfig(
            callable=fn,
            description=desc,
            parameters=parameters or {"type": "object", "properties": {}},
        )
        return fn
    return decorator


@_register(
    "search_company",
    "根据关键词搜索企业全称，返回匹配的企业名称列表",
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "企业名称关键词，如'海康'、'大华'"}
        },
        "required": ["keyword"]
    },
)
def _search(keyword: str) -> dict:
    from app.repositories.company_repo import search_companies
    from app.repositories.financial_repo import resolve_full_name
    from app.services.tianyancha_client import fetch_company

    results = search_companies(keyword)
    if not results:
        try:
            fetch_company(keyword)
        except Exception:
            pass
        results = search_companies(keyword)

    if not results:
        full_name = resolve_full_name(keyword)
        if full_name and full_name != keyword:
            try:
                fetch_company(full_name)
            except Exception:
                pass
            results = search_companies(keyword)
            if not results:
                results = search_companies(full_name)

    return {"keyword": keyword, "count": len(results), "results": results}


@_register(
    "assess_risk",
    "评估供应商风险，返回风险评分、财报、风险明细",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"}
        },
        "required": ["company_name"]
    },
)
def _assess(company_name: str) -> dict:
    from app.schemas import RiskAssessRequest
    from app.services.risk_service import assess_risk
    result = assess_risk(RiskAssessRequest(company_name=company_name))
    return result.model_dump()


@_register(
    "check_alert",
    "查看企业预警变化",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"}
        },
        "required": ["company_name"]
    },
)
def _check(company_name: str) -> dict:
    from app.services.alert_service import detect_changes, get_latest_snapshot
    if not get_latest_snapshot(company_name):
        return {"message": "暂无历史快照"}
    return detect_changes(company_name)


@_register(
    "get_watchlist",
    "获取监控清单",
)
def _list(**kwargs) -> dict:
    from app.services.alert_service import get_watchlist
    companies = get_watchlist()
    return {"count": len(companies), "companies": companies}


@_register(
    "add_to_watchlist",
    "将企业加入监控清单",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"}
        },
        "required": ["company_name"]
    },
)
def _watch(company_name: str) -> dict:
    from app.services.alert_service import add_to_watchlist
    return add_to_watchlist(company_name)


@_register(
    "remove_from_watchlist",
    "将企业从监控清单移除",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"}
        },
        "required": ["company_name"]
    },
)
def _unwatch(company_name: str) -> dict:
    from app.services.alert_service import remove_from_watchlist
    return remove_from_watchlist(company_name)


@_register(
    "esg_assessment",
    "评估企业ESG风险（环境/社会/治理三维评分）",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"}
        },
        "required": ["company_name"]
    },
)
def _esg(company_name: str) -> dict:
    from app.services.esg_service import assess_esg
    result = assess_esg(company_name)
    if result is None:
        return {"error": "未找到企业数据"}
    return result


@_register(
    "contagion_analysis",
    "分析企业风险传染路径（分支机构/供应链/同行业）",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"}
        },
        "required": ["company_name"]
    },
)
def _contagion(company_name: str) -> dict:
    from app.services.contagion import analyze_contagion
    return analyze_contagion(company_name)


@_register(
    "sentiment_analysis",
    "分析企业舆情情感（新闻搜索+LLM分析）",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"}
        },
        "required": ["company_name"]
    },
)
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


@_register(
    "predict_risk",
    "预测企业未来6-12月风险恶化概率",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"}
        },
        "required": ["company_name"]
    },
)
def _predict(company_name: str) -> dict:
    from app.services.predictor import predict_company
    result = predict_company(company_name)
    if result is None:
        return {"error": "未找到企业数据"}
    return result


@_register(
    "macro_risk",
    "分析企业宏观风险（行业PMI景气+地区信用+政策标签）",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"}
        },
        "required": ["company_name"]
    },
)
def _macro(company_name: str) -> dict:
    from app.services.macro_service import assess_macro_risk
    return assess_macro_risk(company_name)


@_register(
    "find_alternatives",
    "为高风险企业推荐同行业低风险替代供应商",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"}
        },
        "required": ["company_name"]
    },
)
def _alternatives(company_name: str) -> dict:
    from app.services.alternative_service import find_alternatives
    return find_alternatives(company_name)


@_register(
    "scenario_simulate",
    "模拟供应商倒闭/诉讼等情景下的影响",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"},
            "scenario": {
                "type": "string",
                "enum": ["bankruptcy", "lawsuit", "disruption", "quality"],
                "description": "情景类型：bankruptcy(破产)/lawsuit(诉讼)/disruption(供应中断)/quality(质量)"
            }
        },
        "required": ["company_name"]
    },
)
def _simulate(company_name: str, scenario: str = "bankruptcy") -> dict:
    from app.services.scenario_service import simulate
    return simulate(company_name, scenario)


@_register(
    "check_sanctions",
    "筛查企业是否在国际制裁/黑名单中（OFAC实体清单/失信等）",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"}
        },
        "required": ["company_name"]
    },
)
def _sanctions(company_name: str) -> dict:
    from app.services.sanctions_service import check_sanctions
    return check_sanctions(company_name)


@_register(
    "knowledge_search",
    "从知识库检索相关文档（财报、合同、ESG报告等）",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词"},
            "company_name": {"type": "string", "description": "企业全称（可选）"}
        },
        "required": ["query"]
    },
)
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


@_register(
    "tianyancha_query",
    "调用天眼查 API 查询企业数据。工商:ic/baseinfo(基本信息)/holder(股东)/invest(对外投资)"
    "/changeInfo(变更)/branch(分支机构)。司法:jr/lawSuit(诉讼)/dishonesty(失信)"
    "/executedPerson(被执行)/courtAnnouncement(开庭)/consumptionRestriction(限消令)。"
    "经营:risk/riskInfo(风险)/mr/abnormal(异常)/punishmentInfo(行政处罚)/illegalinfo(严重违法)"
    "/equityPledge(股权出质)/taxArrears(欠税)。知产:ipr/tm(商标)/patent(专利)。"
    "新闻:news/newsList。完整路径需前缀 /services/open/",
    parameters={
        "type": "object",
        "properties": {
            "endpoint": {"type": "string", "description": "天眼查 API 路径"},
            "keyword": {"type": "string", "description": "企业名称关键词"}
        },
        "required": ["endpoint", "keyword"]
    },
)
def _tianyancha_query(endpoint: str, keyword: str) -> dict:
    from app.services.tianyancha_client import query
    result = query(endpoint, keyword)
    if result is None:
        return {"error": "API 调用失败或无数据", "endpoint": endpoint, "keyword": keyword}
    return {"endpoint": endpoint, "keyword": keyword, "data": result}


@_register(
    "create_sourcing_request",
    "创建采购寻源请求，后续可执行搜索。",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "需求标题"},
            "category": {"type": "string", "description": "采购品类"},
            "spec": {"type": "string", "description": "规格/技术要求"},
        },
        "required": ["title", "category", "spec"],
    },
)
def _create_sourcing_request(title: str, category: str, spec: str) -> dict:
    from app.schemas.sourcing import SourcingRequestInput
    from app.services.sourcing_service import create_sourcing_request as _create
    rid = _create(SourcingRequestInput(title=title, category=category, spec=spec), user_id="agent")
    return {"request_id": rid, "status": "created"}


@_register(
    "search_suppliers",
    "执行供应商搜索、风险评估和排序，返回 Top-10 候选。",
    parameters={
        "type": "object",
        "properties": {
            "request_id": {"type": "string", "description": "寻源请求 ID"},
        },
        "required": ["request_id"],
    },
)
def _search_suppliers(request_id: str) -> dict:
    from app.services.sourcing_service import search_suppliers as _search
    return _search(request_id)


@_register(
    "select_sourcing_result",
    "勾选寻源结果：加入监控列表或申请准入。",
    parameters={
        "type": "object",
        "properties": {
            "result_id": {"type": "string", "description": "寻源结果 ID"},
            "action": {"type": "string", "description": "watchlist 或 apply_access"},
        },
        "required": ["result_id"],
    },
)
def _select_sourcing_result(result_id: str, action: str = "watchlist") -> dict:
    from app.services.sourcing_service import select_result as _select
    return _select(result_id, action, user_id="agent")


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


# ---- ReAct loop (function calling) ----

SYSTEM_PROMPT = """你是采购风险分析专家。

核心规则：
1. **必须先调用工具获取数据，严禁凭空编造数据**
2. 回答要简洁，控制在 300 字以内
3. 使用中文回答

分析流程：
- 看风险：先用 search_company 搜全名，再用 assess_risk
- 看ESG：用 esg_assessment 获取环境/社会/治理三维评分
- 看传染：用 contagion_analysis 查关联方和供应链风险
- 看舆情：用 sentiment_analysis 看新闻情感趋势
- 看预测：用 predict_risk 看未来风险恶化概率
- 看宏观：用 macro_risk 看行业景气+地区风险+政策标签
- 找替代：用 find_alternatives 为高风险企业推荐同行业低风险供应商
- 情景模拟：用 scenario_simulate 模拟供应商倒闭/诉讼等影响
- 制裁筛查：用 check_sanctions 查国际制裁/失信/黑名单
- 知识库检索：用 knowledge_search 检索上传的文档
- 要对比多家：先 get_watchlist，再逐个 assess_risk

业务规则：
- assess_risk 已含财报数据，上市公司要分析财报
- debt_ratio=0 表示数据缺失（港股），不要解读为低负债
- in_watchlist=true 表示已在监控，不要建议"加入监控"
- 综合问题可调多个工具（ESG+风险+舆情）
- 搜不到就告知用户
- 不同工具返回的数据如有矛盾，直接指出差异，不要自行编造理由解释

监控列表操作：
- 查看监控列表：使用 get_watchlist
- 添加监控：使用 add_to_watchlist
- 移除监控：使用 remove_from_watchlist"""


def _build_tools_schema() -> list[dict]:
    """将 TOOLS 转换为 OpenAI function calling tools schema。"""
    schemas = []
    for name, config in TOOLS.items():
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": config.description,
                "parameters": config.parameters,
            }
        })
    return schemas


def _execute_tool_sync(tool_name: str, tool_args: dict) -> str:
    """同步执行工具调用，返回 JSON 字符串。"""
    if tool_name not in TOOLS:
        return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)
    try:
        config = TOOLS[tool_name]
        raw_result = config.callable(**tool_args)
        result_str = json.dumps(raw_result, ensure_ascii=False, default=str)
        # 截断过长结果
        if len(result_str) > config.max_result_tokens * 3:
            result_str = result_str[:config.max_result_tokens * 3] + "\n\n[结果过长，已截断]"
        return result_str
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def _execute_tool_async(tool_name: str, tool_args: dict, retry: int = 0) -> str:
    """异步执行工具调用，支持自动重试。"""
    if tool_name not in TOOLS:
        return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)
    try:
        config = TOOLS[tool_name]
        raw_result = await asyncio.to_thread(config.callable, **tool_args)
        result_str = json.dumps(raw_result, ensure_ascii=False, default=str)
        if len(result_str) > config.max_result_tokens * 3:
            result_str = result_str[:config.max_result_tokens * 3] + "\n\n[结果过长，已截断]"
        return result_str
    except Exception as e:
        if retry < 1:
            await asyncio.sleep(2 ** retry)
            return await _execute_tool_async(tool_name, tool_args, retry + 1)
        return json.dumps({
            "error": str(e),
            "suggestion": f"工具 {tool_name} 执行失败，请检查参数是否正确",
        }, ensure_ascii=False)


def chat(session_id: str, message: str) -> str:
    history = _load_history(session_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history[-20:])
    messages.append({"role": "user", "content": message})

    tools_schema = _build_tools_schema()
    tool_call_count = 0

    for _ in range(MAX_ITERATIONS):
        resp = _get_client().chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools_schema,
            tool_choice="auto",
            temperature=0,
        )
        msg = resp.choices[0].message

        # 有工具调用
        if msg.tool_calls:
            messages.append(msg.model_dump())
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                result = _execute_tool_sync(tool_name, tool_args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
                tool_call_count += 1
            continue

        # 无工具调用 = 最终回答
        answer = msg.content or ""

        # 要求至少一次工具调用（分析类问题）
        if tool_call_count == 0 and any(kw in message for kw in ["风险", "评估", "分析", "财务", "诉讼", "监控", "查", "看", "对比"]):
            messages.append({"role": "assistant", "content": answer})
            messages.append({"role": "user", "content": "请先用工具获取真实数据，不要直接编造内容。"})
            continue

        _save_turn(session_id, message, answer)
        return answer

    # 超过最大迭代，强制回答
    messages.append({"role": "user", "content": "你已经调用了足够多的工具，请基于已有数据直接回答用户问题。"})
    resp = _get_client().chat.completions.create(
        model=MODEL, messages=messages, temperature=0,
    )
    answer = resp.choices[0].message.content or "抱歉，无法生成回答。"
    _save_turn(session_id, message, answer)
    return answer


# ---- Streaming (real token-level) ----

async def chat_stream(session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    流式 ReAct 循环，使用原生 function calling + 真实 token 级流式输出。
    Events: thinking, tool_call, tool_result, answer_chunk, done, error
    """
    history = _load_history(session_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history[-20:])
    messages.append({"role": "user", "content": message})

    tools_schema = _build_tools_schema()
    tool_call_count = 0
    full_answer = ""

    try:
        for iteration in range(MAX_ITERATIONS):
            yield _sse_event("thinking", {"iteration": iteration + 1, "message": f"思考中... (第 {iteration + 1} 轮)"})

            # 使用 stream=True 获取真实流式输出
            stream = await asyncio.to_thread(
                _get_client().chat.completions.create,
                model=MODEL,
                messages=messages,
                tools=tools_schema,
                tool_choice="auto",
                temperature=0,
                stream=True,
            )

            # 累积 tool_calls 和 content
            accumulated_tool_calls: dict[int, dict] = {}  # index -> {id, name, arguments}
            content_buffer = ""

            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue

                # 处理 tool_calls 增量
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc_delta.id:
                            accumulated_tool_calls[idx]["id"] = tc_delta.id
                        if tc_delta.function and tc_delta.function.name:
                            accumulated_tool_calls[idx]["name"] = tc_delta.function.name
                        if tc_delta.function and tc_delta.function.arguments:
                            accumulated_tool_calls[idx]["arguments"] += tc_delta.function.arguments

                # 处理 content 增量（逐 token 推送）
                if delta.content:
                    content_buffer += delta.content
                    yield _sse_event("answer_chunk", {"text": delta.content})

            # 如果有 tool_calls，执行工具
            if accumulated_tool_calls:
                # 构建 assistant 消息
                assistant_msg = {"role": "assistant", "content": content_buffer or None, "tool_calls": []}
                for idx in sorted(accumulated_tool_calls.keys()):
                    tc = accumulated_tool_calls[idx]
                    assistant_msg["tool_calls"].append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    })
                messages.append(assistant_msg)

                # 并行执行所有工具调用
                tool_tasks = []
                tool_ids = []
                tool_names = []
                tool_args_list = []
                for tc in assistant_msg["tool_calls"]:
                    tool_name = tc["function"]["name"]
                    try:
                        tool_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        tool_args = {}

                    yield _sse_event("tool_call", {"tool": tool_name, "args": tool_args})
                    tool_tasks.append(_execute_tool_async(tool_name, tool_args))
                    tool_ids.append(tc["id"])
                    tool_names.append(tool_name)
                    tool_args_list.append(tool_args)

                results = await asyncio.gather(*tool_tasks, return_exceptions=True)

                for tc_id, tool_name, result in zip(tool_ids, tool_names, results):
                    content = str(result) if not isinstance(result, Exception) else json.dumps({"error": str(result)}, ensure_ascii=False)
                    messages.append({"role": "tool", "tool_call_id": tc_id, "content": content})
                    yield _sse_event("tool_result", {"tool": tool_name, "result": content})

                tool_call_count += len(tool_names)
                continue

            # 无 tool_calls = 最终回答
            full_answer = content_buffer
            break

        else:
            # 超过最大迭代，强制回答
            messages.append({"role": "user", "content": "请基于已有数据直接回答。"})
            resp = await asyncio.to_thread(
                _get_client().chat.completions.create,
                model=MODEL, messages=messages, temperature=0,
            )
            full_answer = resp.choices[0].message.content or "抱歉，无法生成回答。"
            yield _sse_event("answer_chunk", {"text": full_answer})

        _save_turn(session_id, message, full_answer)
        yield _sse_event("done", {"answer": full_answer})

    except Exception as e:
        yield _sse_event("error", {"message": f"发生错误: {str(e)}"})


def _sse_event(event_type: str, data: dict) -> str:
    """Format data as SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"



