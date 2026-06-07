"""
舆情监控服务 —— 基于已有风险数据的 LLM 情感分析 + 趋势追踪。

数据流：
  已有 MongoDB 风险数据 → 提取风险事件 → LLM 情感分析 → MongoDB(分析结果) → API/告警

数据源：
  - riskInfo: 天眼风险信息（被执行、失信、裁判文书、开庭公告等）
  - lawSuit: 法律诉讼
  - punishmentInfo: 行政处罚
  - abnormal: 经营异常
  - illegalinfo: 严重违法

情感分类：
  - negative: 风险事件（诉讼、处罚、被执行等）
  - neutral:  中性事件（法人变更等）
  - positive: 无（风险数据天然偏负面）

风险标签：财务风险、法律风险、经营风险、合规风险、舆论风险。
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

# ---- LLM prompt ----

SENTIMENT_PROMPT = """你是一个企业风险舆情分析师。以下是一家企业的风险事件列表（来自天眼查等公开数据源）。
请分析该企业的整体舆情状况，返回 JSON 对象（不要 markdown 包裹）：

{
  "overall_sentiment": "negative" | "neutral",
  "sentiment_score": -1.0 到 0.0 （全部为负或中性，-1=极度负面），
  "risk_tags": [{"tag": "标签", "count": 次数}],
  "summary": "一句话舆情总结（30字以内）",
  "key_concerns": ["最值得关注的问题1", "问题2"],
  "articles": [
    {
      "title": "事件标题",
      "sentiment": "negative" | "neutral",
      "confidence": 0.0-1.0,
      "risk_tags": ["风险标签"],
      "summary": "一句话摘要"
    }
  ]
}

风险标签：财务风险、法律风险、经营风险、合规风险、重大诉讼、被执行/失信

输入风险事件：
"""


def _call_llm(prompt: str) -> dict | None:
    """调用 LLM 进行情感分析，返回结构化结果。"""
    if not os.getenv("LLM_API_KEY"):
        return None
    try:
        resp = _get_llm().chat.completions.create(
            model=SENTIMENT_MODEL,
            messages=[
                {"role": "system", "content": "你是一个精确的风险分析器。只返回 JSON 对象，不要任何额外文本。"},
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


def _extract_risk_events(company_name: str) -> list[dict]:
    """从 MongoDB 已有风险数据中提取风险事件列表。"""
    db = get_db()

    events = []

    # ---- riskInfo: 天眼风险信息 ----
    risk_doc = db["riskInfo"].find_one({"name": company_name})
    if risk_doc:
        result = (risk_doc.get("item") or {}).get("result") or {}
        risk_list = result.get("riskList", [])
        for category in risk_list:
            cat_name = category.get("title", "风险")
            for sub in category.get("list", []):
                title = sub.get("title", "")
                total = sub.get("total", 0) or 0
                if total > 0:
                    events.append({
                        "title": f"{cat_name} - {title}：{total}条",
                        "category": cat_name,
                        "type": title,
                        "count": total,
                        "source": "天眼风险",
                    })

    # ---- lawSuit: 法律诉讼 ----
    lawsuit_doc = db["lawSuit"].find_one({"name": company_name})
    if lawsuit_doc:
        result = (lawsuit_doc.get("items") or {}).get("result") or {}
        total = result.get("total", 0) if isinstance(result, dict) else 0
        if total > 0:
            # try to get individual lawsuit titles
            items = result.get("items") or []
            if items and isinstance(items, list):
                for item in items[:10]:
                    title = item.get("title") or item.get("caseNo") or item.get("caseReason") or ""
                    if title:
                        events.append({
                            "title": f"裁判文书：{title}",
                            "category": "司法风险",
                            "type": "裁判文书",
                            "count": 1,
                            "source": "法律诉讼",
                        })
            if not items or total > len(items):
                events.append({
                    "title": f"裁判文书：共{total}条",
                    "category": "司法风险",
                    "type": "裁判文书",
                    "count": total,
                    "source": "法律诉讼",
                })

    # ---- punishmentInfo: 行政处罚 ----
    punish_doc = db["punishmentInfo"].find_one({"name": company_name})
    if punish_doc:
        result = (punish_doc.get("items") or {}).get("result") or {}
        total = result.get("total", 0) if isinstance(result, dict) else 0
        if total > 0:
            events.append({
                "title": f"行政处罚：共{total}条",
                "category": "经营风险",
                "type": "行政处罚",
                "count": total,
                "source": "行政处罚",
            })

    # ---- abnormal: 经营异常 ----
    abnormal_doc = db["abnormal"].find_one({"name": company_name})
    if abnormal_doc:
        result = (abnormal_doc.get("items") or {}).get("result") or {}
        total = result.get("total", 0) if isinstance(result, dict) else 0
        if total > 0:
            events.append({
                "title": f"经营异常：共{total}条",
                "category": "经营风险",
                "type": "经营异常",
                "count": total,
                "source": "经营异常",
            })

    # ---- illegalinfo: 严重违法 ----
    illegal_doc = db["illegalinfo"].find_one({"name": company_name})
    if illegal_doc:
        result = (illegal_doc.get("items") or {}).get("result") or {}
        total = result.get("total", 0) if isinstance(result, dict) else 0
        if total > 0:
            events.append({
                "title": f"严重违法：共{total}条",
                "category": "合规风险",
                "type": "严重违法",
                "count": total,
                "source": "严重违法",
            })

    return events


def _sentiment_score(articles: list[dict]) -> float:
    """计算整体情感得分：[-1, 1]。"""
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
    """聚合风险标签。"""
    tag_counts: dict[str, int] = {}
    for a in articles:
        for tag in a.get("risk_tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return sorted(
        [{"tag": t, "count": c} for t, c in tag_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )


def _event_count(company_name: str) -> int:
    """计算企业的风险事件总数。"""
    events = _extract_risk_events(company_name)
    return sum(e.get("count", 0) for e in events)


def analyze_sentiment(company_name: str, force_refresh: bool = False) -> dict | None:
    """分析单个企业的舆情/风险情感。"""
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

    # extract risk events from existing data
    events = _extract_risk_events(company_name)

    if not events:
        # no risk events at all
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
            "summary": "暂无风险事件记录",
            "key_concerns": [],
            "has_data": False,
        }
        _save_sentiment(company_name, result)
        return result

    # call LLM for analysis
    event_texts = [e["title"] for e in events[:30]]
    prompt = SENTIMENT_PROMPT + json.dumps(event_texts, ensure_ascii=False)
    llm_result = _call_llm(prompt)

    if llm_result and isinstance(llm_result, dict):
        # use LLM result
        articles = llm_result.get("articles", [])
        overall_score = llm_result.get("sentiment_score", 0.0)
        risk_tags = llm_result.get("risk_tags", [])
        summary = llm_result.get("summary", "")
        key_concerns = llm_result.get("key_concerns", [])
    else:
        # fallback: classify all as negative with basic tagging
        articles = []
        for e in events[:20]:
            tag_list = []
            cat = e.get("category", "")
            if "司法" in cat or "诉讼" in cat:
                tag_list = ["法律风险"]
            elif "经营" in cat:
                tag_list = ["经营风险"]
            elif "合规" in cat or "违法" in cat:
                tag_list = ["合规风险"]
            articles.append({
                "title": e["title"],
                "sentiment": "negative",
                "confidence": 0.8,
                "risk_tags": tag_list,
                "summary": e["type"],
            })
        overall_score = -0.3
        risk_tags = _extract_risk_tags(articles)
        summary = f"共{len(events)}条风险事件"
        key_concerns = []

    # aggregate counts
    neg = sum(1 for a in articles if a.get("sentiment") == "negative")
    neu = sum(1 for a in articles if a.get("sentiment") == "neutral")
    pos = sum(1 for a in articles if a.get("sentiment") == "positive")

    result = {
        "company_name": company_name,
        "analyzed_at": datetime.now(timezone.utc),
        "articles_count": len(articles),
        "negative_count": neg,
        "neutral_count": neu,
        "positive_count": pos,
        "sentiment_score": overall_score,
        "risk_tags": risk_tags,
        "articles": articles[:20],
        "summary": summary,
        "key_concerns": key_concerns,
        "total_events": sum(e.get("count", 0) for e in events),
        "has_data": True,
    }

    _save_sentiment(company_name, result)

    # check for negative sentiment alert
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
    """当风险事件显著增加时触发告警。"""
    if not result.get("has_data"):
        return

    total_events = result.get("total_events", 0)
    if total_events == 0:
        return

    db = get_db()
    prev_docs = list(
        db["sentiment_results"]
        .find({"company_name": company_name})
        .sort("analyzed_at", -1)
        .limit(2)
    )

    prev_events = 0
    if len(prev_docs) >= 2:
        prev_events = prev_docs[1].get("total_events", 0)

    # alert on significant increase
    should_alert = False
    alert_reason = ""

    if total_events >= 10 and prev_events == 0:
        should_alert = True
        alert_reason = f"首次检出 {total_events} 条风险事件"
    elif prev_events > 0 and total_events - prev_events >= 5:
        should_alert = True
        alert_reason = f"风险事件激增：{prev_events} → {total_events}（+{total_events-prev_events}）"

    if should_alert:
        risk_tags = [t["tag"] for t in result.get("risk_tags", [])[:3]]
        db["alerts"].insert_one({
            "company_name": company_name,
            "created_at": datetime.now(timezone.utc),
            "changes": [{
                "field": "舆情风险",
                "old": f"风险事件 {prev_events} 条",
                "new": alert_reason,
            }],
            "severity": "warning",
            "type": "sentiment",
            "risk_tags": risk_tags,
        })

        from app.services.feishu import send_alert_card
        send_alert_card(company_name, "warning", [{
            "field": "舆情风险",
            "old": f"风险事件 {prev_events} 条",
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
            "total_events": d.get("total_events", 0),
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
                "total_events": r.get("total_events", 0),
            })
    return results
