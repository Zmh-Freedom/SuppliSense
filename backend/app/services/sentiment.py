"""
舆情监控服务 —— 纯新闻搜索 + LLM 情感分析。

数据流：
  DuckDuckGo / Bing 搜索新闻 → LLM 情感分类 → MongoDB → API / 前端

与「风险评估」的分工：
  - 风险评估：结构化数据（财报 15 指标 + 司法经营 8 指标）→ 0-100 评分
  - 舆情监控：非结构化数据（外部新闻）→ 情感趋势 + AI 摘要

情感范围：-1.0（极度负面）~ +1.0（极度正面），0=中性。
"""

import json
import os
from datetime import datetime, timezone

from openai import OpenAI

from app.core.cache import cached, invalidate_cache
from app.db.mongo import get_db

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

# ---- LLM prompt ----

NEWS_SENTIMENT_PROMPT = """你是一个企业舆情分析师。请分析以下新闻，返回 JSON 对象（不要 markdown）：

{
  "overall_sentiment": "negative" | "neutral" | "positive",
  "sentiment_score": -1.0 到 1.0（正=利好，负=负面，0=中性），
  "summary": "一句话舆情总结（30字以内）",
  "key_concerns": ["最值得关注的风险点1", "风险点2"],
  "risk_tags": [{"tag": "风险标签", "count": 次数}],
  "articles": [
    {
      "index": 原文序号,
      "sentiment": "negative" | "neutral" | "positive",
      "confidence": 0.0-1.0,
      "risk_tags": ["匹配的风险标签"],
      "summary": "20字摘要"
    }
  ]
}

风险标签可选：财务风险、法律风险、经营风险、合规风险、舆论风险、利好信号
如果某条新闻没有明显风险或利好，risk_tags 用空数组 []。

输入新闻（JSON 数组 [{index, title, body}]）：
"""


def _call_llm(prompt: str) -> dict | None:
    """调用 LLM，返回结构化结果。"""
    if not os.getenv("LLM_API_KEY"):
        return None
    try:
        resp = _get_llm().chat.completions.create(
            model=SENTIMENT_MODEL,
            messages=[
                {"role": "system", "content": "你是一个精确的分析器。只返回 JSON 对象，不要额外文本。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
        text = resp.choices[0].message.content or "{}"
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("\n", 1)[0]
        return json.loads(text)
    except Exception:
        return None


# ---- News search ----

def _search_news(company_name: str, max_results: int = 12) -> list[dict]:
    """通过 DuckDuckGo 搜索公司新闻。"""
    import requests
    from bs4 import BeautifulSoup

    articles: list[dict] = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # method 1: DDG HTML endpoint
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": f"{company_name} 新闻", "iar": "news"},
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for res in soup.select(".result__body"):
                title_el = res.select_one(".result__title")
                snippet_el = res.select_one(".result__snippet")
                link_el = res.select_one(".result__url") or res.select_one("a[href]")

                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue

                body = snippet_el.get_text(strip=True) if snippet_el else ""
                url = ""
                if link_el:
                    url = link_el.get("href", "")
                    if "uddg=" in url:
                        from urllib.parse import parse_qs, urlparse
                        parsed = urlparse(url)
                        qs = parse_qs(parsed.query)
                        url = qs.get("uddg", [url])[0]

                articles.append({"title": title, "body": body[:200], "source": "", "url": url, "date": ""})

            if articles:
                return articles[:max_results]
    except Exception:
        pass

    # method 2: ddgs library
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.news(company_name[:6], region="cn-zh", max_results=max_results))
        for r in results:
            title = r.get("title", "")
            if title:
                articles.append({
                    "title": title, "body": r.get("body", "")[:200],
                    "source": r.get("source", ""), "url": r.get("url", ""),
                    "date": r.get("date", ""),
                })
        if articles:
            return articles[:max_results]
    except Exception:
        pass

    return []


# ---- Analysis helpers ----

def _sentiment_score(articles: list[dict]) -> float:
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
    tag_counts: dict[str, int] = {}
    for a in articles:
        for tag in a.get("risk_tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return sorted(
        [{"tag": t, "count": c} for t, c in tag_counts.items()],
        key=lambda x: x["count"], reverse=True,
    )


# ---- Core API ----

def _get_cached_sentiment(company_name: str) -> dict | None:
    """仅返回缓存的舆情数据（不论是否过期），不触发外部调用。"""
    db = get_db()
    cached = db["sentiment_results"].find_one(
        {"company_name": company_name}, sort=[("analyzed_at", -1)]
    )
    if cached:
        cached_at = cached["analyzed_at"]
        if hasattr(cached_at, "replace") and cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        cached["_id"] = str(cached["_id"])
        cached["analyzed_at"] = cached_at.isoformat()
        cached["cache_age_hours"] = round(
            (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600, 1
        )
        cached["is_stale"] = cached["cache_age_hours"] >= 6
        return cached
    return None


# 跟踪正在后台分析的企业，避免重复触发
_analyzing_locks: set[str] = set()


def analyze_sentiment(company_name: str, force_refresh: bool = False) -> dict | None:
    """搜索公司新闻并用 LLM 分析舆情情感。缓存 6 小时。"""
    db = get_db()

    # cache check - 取最新一条记录
    if not force_refresh:
        cached = _get_cached_sentiment(company_name)
        if cached and not cached.get("is_stale", False):
            return cached

    # search news
    news_articles = _search_news(company_name)

    if not news_articles:
        result = {
            "company_name": company_name,
            "analyzed_at": datetime.now(timezone.utc),
            "articles_count": 0, "negative_count": 0, "neutral_count": 0, "positive_count": 0,
            "sentiment_score": 0.0, "risk_tags": [], "articles": [],
            "summary": "暂无相关新闻", "key_concerns": [],
            "has_data": False,
        }
        _save_sentiment(company_name, result)
        return result

    # LLM classify
    prompt_data = [
        {"index": i, "title": a["title"], "body": a["body"][:100]}
        for i, a in enumerate(news_articles)
    ]
    prompt = NEWS_SENTIMENT_PROMPT + json.dumps(prompt_data, ensure_ascii=False)
    llm_result = _call_llm(prompt)

    # merge results
    if isinstance(llm_result, dict):
        class_map = {}
        for c in llm_result.get("articles", []):
            if isinstance(c, dict) and "index" in c:
                class_map[c["index"]] = c

        articles = []
        for i, a in enumerate(news_articles):
            cls = class_map.get(i, {})
            articles.append({
                "title": a["title"],
                "body": a["body"][:150],
                "source": a["source"],
                "url": a["url"],
                "date": a["date"],
                "sentiment": cls.get("sentiment", "neutral"),
                "confidence": cls.get("confidence", 0.5),
                "risk_tags": cls.get("risk_tags", []),
                "summary": cls.get("summary", a["title"][:20]),
            })

        overall_score = llm_result.get("sentiment_score", _sentiment_score(articles))
        summary = llm_result.get("summary", "")
        key_concerns = llm_result.get("key_concerns", [])
        risk_tags = llm_result.get("risk_tags", _extract_risk_tags(articles))
    else:
        # LLM failed, use basic classification
        articles = [{
            **a,
            "sentiment": "neutral",
            "confidence": 0.5,
            "risk_tags": [],
            "summary": a["title"][:20],
        } for a in news_articles]
        overall_score = 0.0
        summary = ""
        key_concerns = []
        risk_tags = []

    # aggregate
    neg = sum(1 for a in articles if a.get("sentiment") == "negative")
    neu = sum(1 for a in articles if a.get("sentiment") == "neutral")
    pos = sum(1 for a in articles if a.get("sentiment") == "positive")

    if not summary:
        if neg > 0:
            summary = f"共{len(articles)}条新闻，{neg}条负面"
        elif pos > len(articles) // 2:
            summary = f"共{len(articles)}条新闻，舆情偏正面"
        else:
            summary = f"共{len(articles)}条新闻，舆情正常"

    if not key_concerns:
        key_concerns = [a["summary"] for a in articles if a.get("sentiment") == "negative"][:3]

    result = {
        "company_name": company_name,
        "analyzed_at": datetime.now(timezone.utc),
        "articles_count": len(articles),
        "negative_count": neg, "neutral_count": neu, "positive_count": pos,
        "sentiment_score": overall_score,
        "risk_tags": risk_tags,
        "articles": articles[:20],
        "summary": summary,
        "key_concerns": key_concerns,
        "has_data": True,
    }

    _save_sentiment(company_name, result)
    _check_negative_alert(company_name, result)

    return result


def _save_sentiment(company_name: str, result: dict) -> None:
    """插入新记录（而非覆盖），保留历史用于趋势对比。"""
    db = get_db()
    result_copy = {k: v for k, v in result.items()}
    result_copy["company_name"] = company_name
    db["sentiment_results"].insert_one(result_copy)


def _check_negative_alert(company_name: str, result: dict) -> None:
    """当负面新闻占比过高或激增时触发告警。"""
    if not result.get("has_data"):
        return

    total = result["articles_count"]
    neg = result["negative_count"]
    if total == 0:
        return

    neg_ratio = neg / total

    db = get_db()
    prev_docs = list(
        db["sentiment_results"].find({"company_name": company_name}).sort("analyzed_at", -1).limit(2)
    )

    prev_ratio = 0.0
    if len(prev_docs) >= 2:
        prev = prev_docs[1]
        prev_total = prev.get("articles_count", 0)
        if prev_total > 0:
            prev_ratio = prev.get("negative_count", 0) / prev_total

    should_alert = False
    alert_reason = ""

    if neg_ratio >= 0.5 and neg >= 3:
        should_alert = True
        alert_reason = f"负面新闻占比 {neg_ratio*100:.0f}%（{neg}/{total}）"
    elif neg_ratio - prev_ratio >= 0.3 and neg >= 2:
        should_alert = True
        alert_reason = f"负面舆情激增：{prev_ratio*100:.0f}% → {neg_ratio*100:.0f}%"

    if should_alert:
        risk_tags = [t["tag"] for t in result.get("risk_tags", [])[:3]]
        db["alerts"].insert_one({
            "company_name": company_name,
            "created_at": datetime.now(timezone.utc),
            "changes": [{"field": "舆情风险", "old": f"负面占比 {prev_ratio*100:.0f}%", "new": alert_reason}],
            "severity": "warning",
            "type": "sentiment",
            "risk_tags": risk_tags,
        })
        from app.services.feishu import send_alert_card
        send_alert_card(company_name, "warning", [{
            "field": "舆情风险", "old": f"负面占比 {prev_ratio*100:.0f}%", "new": alert_reason,
        }])


# ---- Trend & Dashboard ----

@cached("sentiment_trend", ttl=1800)  # 30 minutes
def get_sentiment_trend(company_name: str) -> dict:
    db = get_db()
    docs = list(
        db["sentiment_results"].find({"company_name": company_name}).sort("analyzed_at", -1).limit(7)
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

    return {
        "company_name": company_name,
        "current": trend[0] if trend else None,
        "trend": trend,
        "trend_direction": _trend_direction(trend),
    }


def _trend_direction(trend: list[dict]) -> str:
    if len(trend) < 2:
        return "stable"
    scores = [t["sentiment_score"] for t in trend[:5]]
    if all(scores[i] <= scores[i + 1] for i in range(len(scores) - 1)):
        return "improving"
    if all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1)):
        return "deteriorating"
    return "stable"


@cached("sentiment_dashboard", ttl=300)  # 5 minutes
def get_sentiment_dashboard() -> dict:
    db = get_db()
    companies = [doc["company_name"] for doc in db["watchlist"].find()]

    # 使用聚合查询一次性获取所有企业的最新舆情（替代 N+1 循环查询）
    snap_map: dict = {}
    if companies:
        pipeline = [
            {"$match": {"company_name": {"$in": companies}}},
            {"$sort": {"analyzed_at": -1}},
            {"$group": {
                "_id": "$company_name",
                "doc": {"$first": "$$ROOT"},
            }},
        ]
        for doc in db["sentiment_results"].aggregate(pipeline):
            snap = doc["doc"]
            snap_map[snap["company_name"]] = snap

    items = []
    for name in companies:
        doc = snap_map.get(name)
        if doc:
            items.append({
                "company_name": name,
                "sentiment_score": doc.get("sentiment_score", 0),
                "negative_count": doc.get("negative_count", 0),
                "articles_count": doc.get("articles_count", 0),
                "top_risk_tags": [t["tag"] for t in doc.get("risk_tags", [])[:3]],
                "summary": doc.get("summary", ""),
                "key_concerns": doc.get("key_concerns", []),
                "analyzed_at": doc["analyzed_at"].isoformat() if isinstance(doc.get("analyzed_at"), datetime) else str(doc.get("analyzed_at", "")),
                "has_data": doc.get("has_data", False),
            })
        else:
            items.append({
                "company_name": name, "sentiment_score": 0, "negative_count": 0,
                "articles_count": 0, "top_risk_tags": [], "summary": "", "key_concerns": [],
                "analyzed_at": None, "has_data": False,
            })

    items.sort(key=lambda x: x["sentiment_score"])
    negative_companies = [i for i in items if i["sentiment_score"] < -0.2]

    return {
        "total_monitored": len(companies),
        "analyzed_count": sum(1 for i in items if i["has_data"]),
        "negative_alert_count": len(negative_companies),
        "companies": items,
        "negative_companies": negative_companies[:10],
    }


def analyze_sentiment_background(company_name: str) -> None:
    """后台执行舆情分析，避免阻塞请求。自动防重复触发。"""
    if company_name in _analyzing_locks:
        return
    _analyzing_locks.add(company_name)
    try:
        analyze_sentiment(company_name, force_refresh=True)
    except Exception:
        pass  # 后台任务失败静默处理
    finally:
        _analyzing_locks.discard(company_name)


def analyze_all_sentiment() -> list[dict]:
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
