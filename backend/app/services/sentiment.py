"""
舆情监控服务 —— 联网搜索新闻 + 风险数据 + LLM 情感分析。

数据流：
  1. DuckDuckGo 搜索公司新闻（免费，无需 API Key）
  2. 合并天眼查风险数据（lawSuit / riskInfo / punishmentInfo）
  3. LLM 情感分析 → MongoDB → API / 告警 / 前端

情感范围：-1.0（极度负面）~ +1.0（极度正面），0=中性。
"""

import json
import os
from datetime import datetime, timezone

from openai import OpenAI

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

# ---- LLM prompts ----

NEWS_SENTIMENT_PROMPT = """你是一个企业舆情分析师。请分析以下新闻，判断每条的情感倾向和风险标签。

规则：
1. sentiment: negative（负面）/ neutral（中性）/ positive（正面）
2. risk_tags（可多选）：财务风险、法律风险、经营风险、合规风险、舆论风险
3. 无明显风险则用空数组 []
4. confidence: 0.0-1.0 置信度

输入是 JSON 数组 [{index: 序号, title: 标题, body: 摘要}]，返回 JSON 数组（不要 markdown）：
[{index: 序号, sentiment: "negative"|"neutral"|"positive", confidence: 0.9, risk_tags: ["标签"], summary: "20字摘要"}]

输入新闻：
"""

RISK_SENTIMENT_PROMPT = """你是一个企业风险舆情分析师。以下是企业的风险事件列表（来自天眼查）。
请分析整体舆情，返回 JSON（不要 markdown）：
{
  "overall_sentiment": "negative" | "neutral",
  "sentiment_score": -1.0 到 0.0（风险数据天然偏负），
  "risk_tags": [{"tag": "标签", "count": 次数}],
  "summary": "一句话总结（30字）",
  "key_concerns": ["最值得关注的问题"],
  "articles": [{"title": "事件", "sentiment": "negative", "confidence": 0.9, "risk_tags": ["标签"], "summary": "摘要"}]
}

风险标签：法律风险、经营风险、合规风险、财务风险、重大诉讼、被执行/失信

输入风险事件：
"""


def _call_llm(prompt: str, expect_list: bool = False) -> dict | list | None:
    """调用 LLM，返回结构化结果。"""
    if not os.getenv("LLM_API_KEY"):
        return None
    try:
        resp = _get_llm().chat.completions.create(
            model=SENTIMENT_MODEL,
            messages=[
                {"role": "system", "content": "你是一个精确的分析器。只返回 JSON，不要额外文本。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
        text = resp.choices[0].message.content or ("[]" if expect_list else "{}")
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("\n", 1)[0]
        return json.loads(text)
    except Exception:
        return None


# ---- News search ----

def _search_news(company_name: str, max_results: int = 10) -> list[dict]:
    """通过 DuckDuckGo HTML 端点搜索公司新闻（绕过 JS API 限流）。"""
    import httpx
    from bs4 import BeautifulSoup

    articles: list[dict] = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # method 1: DDG HTML endpoint (more lenient rate limiting)
    try:
        resp = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": f"{company_name} 新闻", "iar": "news"},
            headers=headers,
            timeout=15,
            follow_redirects=True,
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
                    # unwrap DDG redirect
                    if "uddg=" in url:
                        from urllib.parse import parse_qs, urlparse
                        parsed = urlparse(url)
                        qs = parse_qs(parsed.query)
                        url = qs.get("uddg", [url])[0]

                articles.append({
                    "title": title,
                    "body": body[:200],
                    "source": "",
                    "url": url,
                    "date": "",
                })

            if articles:
                return articles[:max_results]
    except Exception:
        pass

    # method 2: Bing News RSS
    try:
        resp = httpx.get(
            "https://www.bing.com/news/search",
            params={"q": company_name, "qft": "sortbydate=1", "format": "rss"},
            headers=headers,
            timeout=15,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            from xml.etree import ElementTree as ET

            root = ET.fromstring(resp.text)
            for item in root.iter("item"):
                title_el = item.find("title")
                title = title_el.text if title_el is not None and title_el.text else ""
                if title:
                    articles.append({
                        "title": title,
                        "body": "",
                        "source": "",
                        "url": "",
                        "date": "",
                    })
            if articles:
                return articles[:max_results]
    except Exception:
        pass

    # method 3: ddgs library (may hit rate limit)
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.news(company_name[:6], region="cn-zh", max_results=max_results))
        for r in results:
            title = r.get("title", "")
            if not title:
                continue
            articles.append({
                "title": title,
                "body": r.get("body", "")[:200],
                "source": r.get("source", ""),
                "url": r.get("url", ""),
                "date": r.get("date", ""),
            })
        if articles:
            return articles[:max_results]
    except Exception:
        pass

    return []


# ---- Risk data extraction ----

def _extract_risk_events(company_name: str) -> list[dict]:
    """从 MongoDB 风险数据中提取事件列表。"""
    db = get_db()
    events = []

    risk_doc = db["riskInfo"].find_one({"name": company_name})
    if risk_doc:
        result = (risk_doc.get("item") or {}).get("result") or {}
        for category in result.get("riskList", []):
            cat_name = category.get("title", "风险")
            for sub in category.get("list", []):
                total = sub.get("total", 0) or 0
                if total > 0:
                    events.append({
                        "title": f"{cat_name}-{sub.get('title', '')}：{total}条",
                        "category": cat_name,
                        "type": sub.get("title", ""),
                        "count": total,
                        "source": "天眼风险",
                    })

    for coll, label, cat in [
        ("lawSuit", "裁判文书", "司法风险"),
        ("punishmentInfo", "行政处罚", "经营风险"),
        ("abnormal", "经营异常", "经营风险"),
        ("illegalinfo", "严重违法", "合规风险"),
    ]:
        doc = db[coll].find_one({"name": company_name})
        if doc:
            result = (doc.get("items") or {}).get("result") or {}
            total = result.get("total", 0) if isinstance(result, dict) else 0
            if total > 0:
                events.append({
                    "title": f"{label}：共{total}条",
                    "category": cat,
                    "type": label,
                    "count": total,
                    "source": coll,
                })
    return events


# ---- Analysis ----

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


def analyze_sentiment(company_name: str, force_refresh: bool = False) -> dict | None:
    """分析单个企业的舆情情感。

    数据源：DuckDuckGo 新闻（主） + 天眼查风险数据（辅）。
    缓存 6 小时。
    """
    db = get_db()

    # cache check
    if not force_refresh:
        cached = db["sentiment_results"].find_one({"company_name": company_name})
        if cached and (datetime.now(timezone.utc) - cached["analyzed_at"]).total_seconds() < 6 * 3600:
            cached["_id"] = str(cached["_id"])
            cached["analyzed_at"] = cached["analyzed_at"].isoformat()
            return cached

    # ---- Phase 1: search news ----
    news_articles = _search_news(company_name)

    # ---- Phase 2: extract risk events ----
    risk_events = _extract_risk_events(company_name)

    all_articles = []
    total_risk_events = sum(e.get("count", 0) for e in risk_events)

    # ---- Phase 3: LLM classify news ----
    news_classified = []
    if news_articles:
        prompt_data = [
            {"index": i, "title": a["title"], "body": a["body"][:100]}
            for i, a in enumerate(news_articles)
        ]
        prompt = NEWS_SENTIMENT_PROMPT + json.dumps(prompt_data, ensure_ascii=False)
        result = _call_llm(prompt, expect_list=True)
        if isinstance(result, list):
            class_map = {}
            for c in result:
                if isinstance(c, dict) and "index" in c:
                    class_map[c["index"]] = c
            for i, a in enumerate(news_articles):
                cls = class_map.get(i, {})
                news_classified.append({
                    "title": a["title"],
                    "body": a["body"][:150],
                    "source": a["source"],
                    "url": a["url"],
                    "date": a["date"],
                    "sentiment": cls.get("sentiment", "neutral"),
                    "confidence": cls.get("confidence", 0.5),
                    "risk_tags": cls.get("risk_tags", []),
                    "summary": cls.get("summary", a["title"][:20]),
                    "source_type": "news",
                })

    # ---- Phase 4: LLM analyze risk events ----
    risk_classified = []
    has_risk_data = len(risk_events) > 0
    if has_risk_data:
        event_texts = [e["title"] for e in risk_events[:30]]
        prompt = RISK_SENTIMENT_PROMPT + json.dumps(event_texts, ensure_ascii=False)
        result = _call_llm(prompt)
        if isinstance(result, dict):
            risk_classified = result.get("articles", [])
            # mark as risk-type
            for a in risk_classified:
                a["source_type"] = "risk"

    # ---- Phase 5: merge results ----
    all_articles = news_classified + risk_classified

    if not all_articles:
        result = {
            "company_name": company_name,
            "analyzed_at": datetime.now(timezone.utc),
            "articles_count": 0,
            "negative_count": 0,
            "neutral_count": 0,
            "positive_count": 0,
            "sentiment_score": 0.0,
            "risk_tags": [],
            "articles": [],
            "summary": "暂无相关新闻和风险数据",
            "key_concerns": [],
            "total_events": total_risk_events,
            "news_count": len(news_articles),
            "has_data": False,
        }
        _save_sentiment(company_name, result)
        return result

    # aggregate
    neg = sum(1 for a in all_articles if a.get("sentiment") == "negative")
    neu = sum(1 for a in all_articles if a.get("sentiment") == "neutral")
    pos = sum(1 for a in all_articles if a.get("sentiment") == "positive")
    overall_score = _sentiment_score(all_articles)
    risk_tags = _extract_risk_tags(all_articles)

    # generate overall summary from LLM result or fallback
    if isinstance(result, dict):  # risk LLM result
        summary = result.get("summary", "")
        key_concerns = result.get("key_concerns", [])
    else:
        summary = ""
        key_concerns = []

    # fallback summary
    if not summary:
        neg_ratio = f"{neg}/{len(all_articles)}" if all_articles else "0"
        summary = f"共{len(all_articles)}条信息，负面{neg_ratio}" if all_articles else "暂无舆情数据"
    if not key_concerns and neg > 0:
        neg_articles = [a for a in all_articles if a.get("sentiment") == "negative"]
        key_concerns = [a.get("summary", a["title"][:20]) for a in neg_articles[:3]]

    result = {
        "company_name": company_name,
        "analyzed_at": datetime.now(timezone.utc),
        "articles_count": len(all_articles),
        "negative_count": neg,
        "neutral_count": neu,
        "positive_count": pos,
        "sentiment_score": overall_score,
        "risk_tags": risk_tags,
        "articles": all_articles[:20],
        "summary": summary,
        "key_concerns": key_concerns,
        "total_events": total_risk_events,
        "news_count": len(news_articles),
        "has_data": True,
    }

    _save_sentiment(company_name, result)
    _check_negative_alert(company_name, result)

    return result


def _save_sentiment(company_name: str, result: dict) -> None:
    db = get_db()
    result_copy = {k: v for k, v in result.items()}
    db["sentiment_results"].update_one(
        {"company_name": company_name},
        {"$set": result_copy},
        upsert=True,
    )


def _check_negative_alert(company_name: str, result: dict) -> None:
    """当负面舆情激增时触发告警。"""
    if not result.get("has_data"):
        return

    total = result["articles_count"]
    neg = result["negative_count"]
    if total == 0:
        return

    neg_ratio = neg / total

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

def get_sentiment_trend(company_name: str) -> dict:
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
            "total_events": d.get("total_events", 0),
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


def get_sentiment_dashboard() -> dict:
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
                "total_events": doc.get("total_events", 0),
                "top_risk_tags": [t["tag"] for t in doc.get("risk_tags", [])[:3]],
                "summary": doc.get("summary", ""),
                "key_concerns": doc.get("key_concerns", []),
                "analyzed_at": doc["analyzed_at"].isoformat() if isinstance(doc.get("analyzed_at"), datetime) else str(doc.get("analyzed_at", "")),
                "has_data": doc.get("has_data", False),
            })
        else:
            items.append({
                "company_name": name,
                "sentiment_score": 0,
                "negative_count": 0,
                "articles_count": 0,
                "total_events": 0,
                "top_risk_tags": [],
                "summary": "",
                "key_concerns": [],
                "analyzed_at": None,
                "has_data": False,
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
                "total_events": r.get("total_events", 0),
            })
    return results
