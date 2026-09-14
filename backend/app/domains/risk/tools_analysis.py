"""分析类工具 — ESG、舆情、传染、制裁、替代、对比、趋势、财务。"""
from langchain_core.tools import tool


@tool
def esg_assessment(company_name: str) -> dict:
    """评估企业ESG风险（环境/社会/治理三维评分）。

    Args:
        company_name: 企业全称
    """
    from app.domains.risk.esg_service import assess_esg
    result = assess_esg(company_name)
    if result is None:
        return {"status": "not_found", "error": "未找到企业数据", "company_name": company_name}
    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(result, tool_name="esg_assessment", entity_id=f"entity:{company_name}", dimension="esg", claim_fields=["total_score", "total_level"])


@tool
def contagion_analysis(company_name: str) -> dict:
    """分析企业风险传染路径（分支机构/供应链/同行业）。

    Args:
        company_name: 企业全称
    """
    from app.domains.risk.contagion import analyze_contagion
    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(
        analyze_contagion(company_name),
        tool_name="contagion_analysis",
        entity_id=f"entity:{company_name}",
        dimension="risk_network",
        claim_fields=[
            "related_count",
            "branch_count",
            "dependency_count",
            "same_industry_count",
            "high_risk_related_count",
        ],
    )


@tool
def sentiment_analysis(company_name: str) -> dict:
    """分析企业舆情情感（新闻搜索+LLM分析）。

    Args:
        company_name: 企业全称
    """
    from app.domains.risk.sentiment import analyze_sentiment
    result = analyze_sentiment(company_name)
    if result is None:
        return {"status": "not_found", "error": "暂无舆情数据", "company_name": company_name}
    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence({
        "company_name": result["company_name"],
        "overall_sentiment": result.get("overall_sentiment", "neutral"),
        "sentiment_score": result["sentiment_score"],
        "negative_count": result["negative_count"],
        "neutral_count": result["neutral_count"],
        "positive_count": result["positive_count"],
        "articles_count": result["articles_count"],
        "summary": result["summary"],
        "key_concerns": result.get("key_concerns", []),
        "risk_tags": [t["tag"] for t in result.get("risk_tags", [])],
        "articles": result.get("articles", []),
        "llm_analyzed": result.get("llm_analyzed"),
        "analysis_mode": result.get("analysis_mode"),
        "has_data": result.get("has_data", False),
    }, tool_name="sentiment_analysis", entity_id=f"entity:{company_name}", dimension="sentiment", claim_fields=[
        "overall_sentiment",
        "sentiment_score",
        "articles_count",
        "negative_count",
        "neutral_count",
        "positive_count",
        "summary",
    ])


@tool
def check_sanctions(company_name: str) -> dict:
    """筛查企业是否在国际制裁/黑名单中（OFAC实体清单/失信等）。

    Args:
        company_name: 企业全称
    """
    from app.domains.risk.sanctions_service import check_sanctions as _check
    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(_check(company_name), tool_name="check_sanctions", entity_id=f"entity:{company_name}", dimension="compliance", claim_fields=["clean", "match_count"])


@tool
def find_alternatives(company_name: str) -> dict:
    """为高风险企业推荐同行业低风险替代供应商。

    Args:
        company_name: 企业全称
    """
    from app.domains.risk.alternative_service import find_alternatives as _find
    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(
        _find(company_name),
        tool_name="find_alternatives",
        entity_id=f"entity:{company_name}",
        dimension="sourcing",
        claim_fields=["source_industry", "source_risk_score", "alternatives_count"],
    )


@tool
def compare_companies(company_names: list[str]) -> dict:
    """对比多家企业的风险状况（风险评分、财务、ESG、舆情）。

    Args:
        company_names: 企业名称列表，如 ["海康威视", "大华股份"]
    """
    from app.db.mongo import get_db

    db = get_db()
    collection_names = db.list_collection_names()
    results = []

    for name in company_names:
        snap = db["alert_snapshots"].find_one(
            {"company_name": name}, sort=[("checked_at", -1)]
        )
        esg = (
            db["esg_results"].find_one({"company_name": name}, sort=[("assessed_at", -1)])
            if "esg_results" in collection_names else None
        )
        sent = (
            db["sentiment_results"].find_one({"company_name": name}, sort=[("analyzed_at", -1)])
            if "sentiment_results" in collection_names else None
        )

        item: dict = {
            "company_name": name,
            "risk_score": snap.get("risk_score") if snap else None,
            "risk_level": snap.get("risk_level") if snap else None,
            "financial": snap.get("financial") if snap else None,
        }
        if esg:
            item["esg"] = {"total_score": esg.get("total_score"), "total_level": esg.get("total_level")}
        if sent:
            item["sentiment"] = {"sentiment_score": sent.get("sentiment_score"), "articles_count": sent.get("articles_count")}
        results.append(item)

    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(
        {"count": len(results), "companies": results},
        tool_name="compare_companies",
        entity_id="comparison",
        dimension="risk_comparison",
        claim_fields=["count"],
    )


@tool
def analyze_trend(company_name: str, period_months: int = 6) -> dict:
    """分析企业风险评分的历史趋势变化。

    Args:
        company_name: 企业全称
        period_months: 分析周期（月），默认 6
    """
    from datetime import datetime, timedelta, timezone
    from app.db.mongo import get_db

    db = get_db()
    days = period_months * 30
    since = datetime.now(timezone.utc) - timedelta(days=days)
    snapshots = list(
        db["alert_snapshots"]
        .find(
            {"company_name": company_name, "checked_at": {"$gte": since}},
            {"checked_at": 1, "risk_score": 1, "risk_level": 1, "_id": 0},
        )
        .sort("checked_at", 1)
    )

    data = [
        {"date": s["checked_at"].strftime("%Y-%m-%d"), "risk_score": s.get("risk_score", 0), "risk_level": s.get("risk_level", "")}
        for s in snapshots
    ]

    trend = "稳定"
    if len(data) >= 2:
        first_score = data[0]["risk_score"]
        last_score = data[-1]["risk_score"]
        delta = last_score - first_score
        if delta > 10:
            trend = "恶化"
        elif delta < -10:
            trend = "改善"

    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(
        {"company_name": company_name, "period_months": period_months, "trend": trend, "data": data},
        tool_name="analyze_trend",
        entity_id=f"entity:{company_name}",
        dimension="risk_trend",
        claim_fields=["trend", "period_months"],
    )


@tool
def query_financials(company_name: str) -> dict:
    """查询企业财务指标（资产负债率、净利润、营收等）。

    Args:
        company_name: 企业全称
    """
    from app.domains.risk.repo_financial import get_financial_metrics

    fin = get_financial_metrics(company_name)
    if fin is None:
        return {"status": "not_found", "error": "未找到财务数据", "company_name": company_name}

    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence({
        "company_name": company_name,
        "revenue_growth": fin.revenue_growth,
        "net_profit_growth": fin.net_profit_growth,
        "debt_ratio": fin.debt_ratio,
        "cash_flow": fin.cash_flow,
        "roe": fin.roe,
        "net_profit_margin": fin.net_profit_margin,
        "current_ratio": fin.current_ratio,
        "quick_ratio": fin.quick_ratio,
    }, tool_name="query_financials", entity_id=f"entity:{company_name}", dimension="financial", claim_fields=[
        "revenue_growth", "net_profit_growth", "debt_ratio", "cash_flow", "roe",
        "net_profit_margin", "current_ratio", "quick_ratio",
    ])
