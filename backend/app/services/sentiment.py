"""
舆情监控服务 —— 新闻采集 + LLM 情感分析 + 趋势追踪。

数据流：
  天眼查新闻 API → MongoDB(新闻原文) → LLM 情感分类 → MongoDB(分析结果) → API/告警

情感分类：
  - negative: 负面新闻（处罚、诉讼、亏损、事故等）
  - neutral:  中性新闻（人事变动、行业动态等）
  - positive: 正面新闻（获奖、签约、融资等）

风险标签自动提取：财务风险、法律风险、经营风险、合规风险、舆论风险。
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from app.db.mongo import get_db
from app.services.tianyancha_client import fetch_news

_llm_client = None


def _get_llm():
    global _llm_client
    if _llm_client is None:
        _llm_client = OpenAI(
            api_key=os.getenv("LLM_API_KEY", ""),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        )
    return _llm_client


SENTIMENT_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# ---- prompt ----

SENTIMENT_PROMPT = """你是一个企业舆情分析师。请分析以下新闻标题，判断每条新闻的情感倾向和风险标签。

规则：
1. 情感倾向：negative（负面）、neutral（中性）、positive（正面）
2. 风险标签（可多选）：
   - 财务风险：亏损、债务、资金链、退市
   - 法律风险：诉讼、被执行、失信、破产
   - 经营风险：停产、裁员、重组、事故
   - 合规风险：处罚、违法、环保、税务
   - 舆论风险：负面报道、消费者投诉、媒体质疑
3. 如果没有明显问题，标签用空数组 []

输入是一组新闻标题（JSON 数组），请返回 JSON 数组（不要 markdown 包裹），每个元素包含：
  - index: 原标题在输入数组中的序号
  - sentiment: "negative" / "neutral" / "positive"
  - confidence: 0.0-1.0
  - risk_tags: 风险标签数组
  - summary: 一句话摘要（20字以内）

输入新闻标题：
"""


def _call_llm(prompt: str) -> list[dict]:
    """调用 LLM 进行情感分类，返回结构化结果列表。"""
    if not os.getenv("LLM_API_KEY"):
        return []
    try:
        resp = _get_llm().chat.completions.create(
            model=SENTIMENT_MODEL,
            messages=[
                {"role": "system", "content": "你是一个精确的 NLP 分析器。只返回 JSON 数组，不要任何额外文本。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
        text = resp.choices[0].message.content or "[]"
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("\n", 1)[0]
        return json.loads(text)
    except Exception:
        return []


def _parse_news_items(news_data: dict) -> list[dict]:
    """从 API 返回的新闻数据中提取标题列表。"""
    items_wrapper = news_data.get("items") or {}
    result = items_wrapper.get("result") or {}

    # try multiple possible field names
    items = result.get("items") or result.get("newsList") or result.get("list") or []
    if not isinstance(items, list):
        items = []

    parsed = []
    for item in items:
        title = item.get("title") or item.get("newsTitle") or ""
        if not title:
            continue
        parsed.append({
            "title": title,
            "publish_time": item.get("publishTime") or item.get("pubTime") or "",
            "source": item.get("source") or "",
            "url": item.get("url") or item.get("newsUrl") or "",
        })
    return parsed


def _sentiment_score(articles: list[dict]) -> float:
    """计算整体情感得分：[-1, 1]，负数=负面，0=中性，正数=正面。"""
    if not articles:
        return 0.0
    total = 0.0
    for a in articles:
        s = a.get("sentiment", "neutral")
        conf = a.get("confidence", 0.5)
        if s == "negative":
            total -= conf
        elif s == "positive":
            total += conf
    return round(total / len(articles), 3)


def _extract_risk_tags(articles: list[dict]) -> list[dict]:
    """聚合所有文章的风险标签，按频次排序。"""
    tag_counts: dict[str, int] = {}
    for a in articles:
        for tag in a.get("risk_tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return sorted(
        [{"tag": t, "count": c} for t, c in tag_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )


def analyze_sentiment(company_name: str, force_refresh: bool = False) -> dict | None:
    """分析单个企业的舆情情感。返回分析结果或 None。"""
    db = get_db()

    # check cache (valid for 6 hours)
    if not force_refresh:
        cached = db["sentiment_results"].find_one({"company_name": company_name})
        if cached:
            age = (datetime.now(timezone.utc) - cached["analyzed_at"]).total_seconds()
            if age < 6 * 3600:
                cached["_id"] = str(cached["_id"])
                cached["analyzed_at"] = cached["analyzed_at"].isoformat()
                return cached

    # try to fetch fresh news
    news_resp = fetch_news(company_name)
    if news_resp is None:
        # no news data, fall back to cached
        cached = db["sentiment_results"].find_one({"company_name": company_name})
        if cached:
            cached["_id"] = str(cached["_id"])
            cached["analyzed_at"] = cached["analyzed_at"].isoformat()
            return cached
        return _empty_sentiment(company_name)

    articles = _parse_news_items(news_resp)
    if not articles:
        result = _empty_sentiment(company_name)
        _save_sentiment(company_name, result)
        return result

    # call LLM for classification
    titles = [a["title"] for a in articles]
    prompt = SENTIMENT_PROMPT + json.dumps(titles, ensure_ascii=False)
    classifications = _call_llm(prompt)

    # merge classifications back to articles
    class_map: dict[int, dict] = {}
    for c in classifications:
        if isinstance(c, dict) and "index" in c:
            class_map[c["index"]] = c

    enriched = []
    for i, a in enumerate(articles):
        cls = class_map.get(i, {})
        enriched.append({
            **a,
            "sentiment": cls.get("sentiment", "neutral"),
            "confidence": cls.get("confidence", 0.5),
            "risk_tags": cls.get("risk_tags", []),
            "summary": cls.get("summary", a["title"][:20]),
        })

    # aggregate
    neg = sum(1 for a in enriched if a["sentiment"] == "negative")
    neu = sum(1 for a in enriched if a["sentiment"] == "neutral")
    pos = sum(1 for a in enriched if a["sentiment"] == "positive")

    result = {
        "company_name": company_name,
        "analyzed_at": datetime.now(timezone.utc),
        "articles_count": len(enriched),
        "negative_count": neg,
        "neutral_count": neu,
        "positive_count": pos,
        "sentiment_score": _sentiment_score(enriched),
        "risk_tags": _extract_risk_tags(enriched),
        "articles": enriched[:20],  # keep top 20
        "has_data": True,
    }

    _save_sentiment(company_name, result)

    # check for negative sentiment alert
    _check_negative_alert(company_name, result)

    return result


def _save_sentiment(company_name: str, result: dict) -> None:
    db = get_db()
    db["sentiment_results"].update_one(
        {"company_name": company_name},
        {"$set": result},
        upsert=True,
    )


def _empty_sentiment(company_name: str) -> dict:
    return {
        "company_name": company_name,
        "analyzed_at": datetime.now(timezone.utc),
        "articles_count": 0,
        "negative_count": 0,
        "neutral_count": 0,
        "positive_count": 0,
        "sentiment_score": 0.0,
        "risk_tags": [],
        "articles": [],
        "has_data": False,
    }


def _check_negative_alert(company_name: str, result: dict) -> None:
    """当负面舆情占比过高时触发告警。"""
    if not result.get("has_data"):
        return
    total = result["articles_count"]
    neg = result["negative_count"]
    if total == 0:
        return

    neg_ratio = neg / total

    # compare with previous to detect surge
    db = get_db()
    prev_docs = list(
        db["sentiment_results"]
        .find({"company_name": company_name})
        .sort("analyzed_at", -1)
        .limit(2)
    )

    prev_ratio = 0.0
    if len(prev_docs) >= 2:
        prev = prev_docs[1]
        prev_total = prev.get("articles_count", 0)
        if prev_total > 0:
            prev_ratio = prev.get("negative_count", 0) / prev_total

    # alert conditions: high negativity OR surge
    should_alert = False
    alert_reason = ""

    if neg_ratio >= 0.5 and neg >= 3:
        should_alert = True
        alert_reason = f"负面舆情占比 {neg_ratio*100:.0f}%（{neg}/{total}）"
    elif neg_ratio - prev_ratio >= 0.3 and neg >= 2:
        should_alert = True
        alert_reason = f"负面舆情激增：{prev_ratio*100:.0f}% → {neg_ratio*100:.0f}%"

    if should_alert:
        risk_tags = [t["tag"] for t in result.get("risk_tags", [])[:3]]
        db["alerts"].insert_one({
            "company_name": company_name,
            "created_at": datetime.now(timezone.utc),
            "changes": [{
                "field": "舆情风险",
                "old": f"负面占比 {prev_ratio*100:.0f}%",
                "new": alert_reason,
            }],
            "severity": "warning",
            "type": "sentiment",
            "risk_tags": risk_tags,
        })

        # push to feishu
        from app.services.feishu import send_alert_card
        send_alert_card(company_name, "warning", [{
            "field": "舆情风险",
            "old": f"负面占比 {prev_ratio*100:.0f}%",
            "new": alert_reason,
        }])


def get_sentiment_trend(company_name: str) -> dict:
    """获取企业舆情趋势（最近 7 次分析结果）。"""
    db = get_db()
    docs = list(
        db["sentiment_results"]
        .find({"company_name": company_name})
        .sort("analyzed_at", -1)
        .limit(7)
    )
    trend = []
    for d in docs:
        trend.append({
            "date": d["analyzed_at"].isoformat() if isinstance(d["analyzed_at"], datetime) else str(d["analyzed_at"]),
            "sentiment_score": d.get("sentiment_score", 0),
            "negative_count": d.get("negative_count", 0),
            "neutral_count": d.get("neutral_count", 0),
            "positive_count": d.get("positive_count", 0),
            "articles_count": d.get("articles_count", 0),
        })

    current = trend[0] if trend else None
    return {
        "company_name": company_name,
        "current": current,
        "trend": trend,
        "trend_direction": _trend_direction(trend),
    }


def _trend_direction(trend: list[dict]) -> str:
    """判断舆情趋势方向。"""
    if len(trend) < 2:
        return "stable"
    scores = [t["sentiment_score"] for t in trend[:5]]
    if all(scores[i] <= scores[i + 1] for i in range(len(scores) - 1)):
        return "improving"
    if all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1)):
        return "deteriorating"
    return "stable"


def get_sentiment_dashboard() -> dict:
    """舆情总览：所有监控企业的舆情概览。"""
    db = get_db()
    companies = [doc["company_name"] for doc in db["watchlist"].find()]

    items = []
    for name in companies:
        doc = db["sentiment_results"].find_one({"company_name": name})
        if doc:
            items.append({
                "company_name": name,
                "sentiment_score": doc.get("sentiment_score", 0),
                "negative_count": doc.get("negative_count", 0),
                "articles_count": doc.get("articles_count", 0),
                "top_risk_tags": [t["tag"] for t in doc.get("risk_tags", [])[:3]],
                "analyzed_at": doc["analyzed_at"].isoformat() if isinstance(doc.get("analyzed_at"), datetime) else str(doc.get("analyzed_at", "")),
                "has_data": doc.get("has_data", False),
            })
        else:
            items.append({
                "company_name": name,
                "sentiment_score": 0,
                "negative_count": 0,
                "articles_count": 0,
                "top_risk_tags": [],
                "analyzed_at": None,
                "has_data": False,
            })

    # sort by sentiment_score (most negative first)
    items.sort(key=lambda x: x["sentiment_score"])

    negative_companies = [i for i in items if i["sentiment_score"] < -0.2]
    total_with_data = sum(1 for i in items if i["has_data"])

    return {
        "total_monitored": len(companies),
        "analyzed_count": total_with_data,
        "negative_alert_count": len(negative_companies),
        "companies": items,
        "negative_companies": negative_companies[:10],
    }


def analyze_all_sentiment() -> list[dict]:
    """分析所有监控企业的舆情。"""
    db = get_db()
    companies = [doc["company_name"] for doc in db["watchlist"].find()]
    results = []
    for name in companies:
        r = analyze_sentiment(name)
        if r:
            results.append({
                "company_name": name,
                "has_data": r.get("has_data", False),
                "articles_count": r.get("articles_count", 0),
                "sentiment_score": r.get("sentiment_score", 0),
                "negative_count": r.get("negative_count", 0),
            })
    return results
