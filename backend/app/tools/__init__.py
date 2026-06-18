"""LangGraph 工具定义 — 包装现有 service 层，供 LangGraph graph 使用。"""

from langchain_core.tools import tool


@tool
def search_company(keyword: str) -> dict:
    """根据关键词搜索企业全称，返回匹配的企业名称列表。

    Args:
        keyword: 企业名称关键词，如'海康'、'大华'
    """
    from app.repositories.company_repo import search_companies
    from app.services.tianyancha_client import fetch_company

    results = search_companies(keyword)
    if not results:
        try:
            fetch_company(keyword)
        except Exception:
            pass
        results = search_companies(keyword)

    return {"keyword": keyword, "count": len(results), "results": results}


@tool
def assess_risk(company_name: str) -> dict:
    """评估供应商风险，返回风险评分、财报、风险明细。

    Args:
        company_name: 企业全称
    """
    from app.schemas import RiskAssessRequest
    from app.services.risk_service import assess_risk as _assess
    result = _assess(RiskAssessRequest(company_name=company_name))
    return result.model_dump()


@tool
def check_alert(company_name: str) -> dict:
    """查看企业预警变化。

    Args:
        company_name: 企业全称
    """
    from app.services.alert_service import detect_changes, get_latest_snapshot
    if not get_latest_snapshot(company_name):
        return {"message": "暂无历史快照"}
    return detect_changes(company_name)


@tool
def get_watchlist() -> dict:
    """获取监控清单。"""
    from app.services.alert_service import get_watchlist as _get_watchlist
    companies = _get_watchlist()
    return {"count": len(companies), "companies": companies}


@tool
def add_to_watchlist(company_name: str) -> dict:
    """将企业加入监控清单。

    Args:
        company_name: 企业全称
    """
    from app.services.alert_service import add_to_watchlist as _add
    return _add(company_name)


@tool
def remove_from_watchlist(company_name: str) -> dict:
    """将企业从监控清单移除。

    Args:
        company_name: 企业全称
    """
    from app.services.alert_service import remove_from_watchlist as _remove
    return _remove(company_name)


@tool
def esg_assessment(company_name: str) -> dict:
    """评估企业ESG风险（环境/社会/治理三维评分）。

    Args:
        company_name: 企业全称
    """
    from app.services.esg_service import assess_esg
    result = assess_esg(company_name)
    if result is None:
        return {"error": "未找到企业数据"}
    return result


@tool
def contagion_analysis(company_name: str) -> dict:
    """分析企业风险传染路径（分支机构/供应链/同行业）。

    Args:
        company_name: 企业全称
    """
    from app.services.contagion import analyze_contagion
    return analyze_contagion(company_name)


@tool
def sentiment_analysis(company_name: str) -> dict:
    """分析企业舆情情感（新闻搜索+LLM分析）。

    Args:
        company_name: 企业全称
    """
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


@tool
def predict_risk(company_name: str) -> dict:
    """预测企业未来6-12月风险恶化概率。

    Args:
        company_name: 企业全称
    """
    from app.services.predictor import predict_company
    result = predict_company(company_name)
    if result is None:
        return {"error": "未找到企业数据"}
    return result


@tool
def macro_risk(company_name: str) -> dict:
    """分析企业宏观风险（行业PMI景气+地区信用+政策标签）。

    Args:
        company_name: 企业全称
    """
    from app.services.macro_service import assess_macro_risk
    return assess_macro_risk(company_name)


@tool
def find_alternatives(company_name: str) -> dict:
    """为高风险企业推荐同行业低风险替代供应商。

    Args:
        company_name: 企业全称
    """
    from app.services.alternative_service import find_alternatives as _find
    return _find(company_name)


@tool
def scenario_simulate(company_name: str, scenario: str = "bankruptcy") -> dict:
    """模拟供应商倒闭/诉讼等情景下的影响。

    Args:
        company_name: 企业全称
        scenario: 情景类型，可选值: bankruptcy(破产), lawsuit(诉讼), disruption(供应中断), quality(质量)
    """
    from app.services.scenario_service import simulate
    return simulate(company_name, scenario)


@tool
def check_sanctions(company_name: str) -> dict:
    """筛查企业是否在国际制裁/黑名单中（OFAC实体清单/失信等）。

    Args:
        company_name: 企业全称
    """
    from app.services.sanctions_service import check_sanctions as _check
    return _check(company_name)


@tool
def knowledge_search(query: str, company_name: str = "") -> dict:
    """从知识库检索相关文档（财报、合同、ESG报告等）。

    Args:
        query: 检索关键词
        company_name: 企业全称（可选）
    """
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


@tool
def generate_report(company_name: str, report_type: str = "excel") -> dict:
    """生成企业风险评估报告（Excel 或 HTML 格式）。

    Args:
        company_name: 企业全称
        report_type: 报告格式，可选值: excel, html
    """
    from app.services.report_service import generate_excel, generate_html_report

    if report_type == "html":
        content = generate_html_report(company_name)
        return {"company_name": company_name, "format": "html", "length": len(content), "content": content}
    else:
        content = generate_excel(company_name)
        return {"company_name": company_name, "format": "excel", "size_bytes": len(content), "message": "Excel 报告已生成"}


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
        {
            "date": s["checked_at"].strftime("%Y-%m-%d"),
            "risk_score": s.get("risk_score", 0),
            "risk_level": s.get("risk_level", ""),
        }
        for s in snapshots
    ]

    # 计算趋势
    trend = "稳定"
    if len(data) >= 2:
        first_score = data[0]["risk_score"]
        last_score = data[-1]["risk_score"]
        delta = last_score - first_score
        if delta > 10:
            trend = "恶化"
        elif delta < -10:
            trend = "改善"

    return {"company_name": company_name, "period_months": period_months, "trend": trend, "data": data}


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
            db["esg_results"].find_one(
                {"company_name": name}, sort=[("assessed_at", -1)]
            )
            if "esg_results" in collection_names
            else None
        )
        sent = (
            db["sentiment_results"].find_one(
                {"company_name": name}, sort=[("analyzed_at", -1)]
            )
            if "sentiment_results" in collection_names
            else None
        )

        item: dict = {
            "company_name": name,
            "risk_score": snap.get("risk_score") if snap else None,
            "risk_level": snap.get("risk_level") if snap else None,
            "financial": snap.get("financial") if snap else None,
        }
        if esg:
            item["esg"] = {
                "total_score": esg.get("total_score"),
                "total_level": esg.get("total_level"),
            }
        if sent:
            item["sentiment"] = {
                "sentiment_score": sent.get("sentiment_score"),
                "articles_count": sent.get("articles_count"),
            }
        results.append(item)

    return {"count": len(results), "companies": results}


@tool
def query_financials(company_name: str) -> dict:
    """查询企业财务指标（资产负债率、净利润、营收等）。

    Args:
        company_name: 企业全称
    """
    from app.repositories.financial_repo import get_financial_metrics

    fin = get_financial_metrics(company_name)
    if fin is None:
        return {"error": "未找到财务数据", "company_name": company_name}

    return {
        "company_name": company_name,
        "revenue_growth": fin.revenue_growth,
        "net_profit_growth": fin.net_profit_growth,
        "debt_ratio": fin.debt_ratio,
        "cash_flow": fin.cash_flow,
        "roe": fin.roe,
        "net_profit_margin": fin.net_profit_margin,
        "current_ratio": fin.current_ratio,
        "quick_ratio": fin.quick_ratio,
    }


@tool
def manage_scheduled_report(action: str, company_names: list[str] | None = None, cron: str = "weekly", report_type: str = "excel") -> dict:
    """管理定时报告任务（创建/查看/删除）。

    Args:
        action: 操作类型，可选值: create, list, delete
        company_names: 监控企业列表（create 时必填）
        cron: 定时表达式，如 weekly, daily（create 时使用）
        report_type: 报告格式，可选值: excel, html
    """
    from app.services.scheduled_report import (
        create_scheduled_report,
        list_scheduled_reports,
        delete_scheduled_report,
    )

    if action == "create":
        if not company_names:
            return {"error": "创建定时报告需要指定企业列表"}
        return create_scheduled_report(company_names, cron, report_type)
    elif action == "list":
        return {"reports": list_scheduled_reports()}
    elif action == "delete":
        if not company_names:
            return {"error": "删除定时报告需要指定 task_id（通过 company_names 传入）"}
        return delete_scheduled_report(company_names[0])
    else:
        return {"error": f"未知操作: {action}，可选值: create, list, delete"}


@tool
def tianyancha_query(endpoint: str, keyword: str) -> dict:
    """调用天眼查 API 查询企业数据。

    常用端点：
      /services/open/ic/baseinfo/normal   基本信息
      /services/open/risk/riskInfo/2.0    风险信息
      /services/open/jr/lawSuit/3.0       法律诉讼
      /services/open/mr/abnormal/2.0       经营异常
      /services/open/mr/punishmentInfo/3.0 行政处罚
      /services/open/mr/illegalinfo/2.0    严重违法
      /services/open/news/newsList/2.0     新闻舆情
      /services/open/ic/branch/2.0         分支机构

    Args:
        endpoint: 天眼查 API 路径
        keyword: 企业名称关键词
    """
    from app.services.tianyancha_client import query

    result = query(endpoint, keyword)
    if result is None:
        return {"error": "API 调用失败", "endpoint": endpoint, "keyword": keyword}
    return {"endpoint": endpoint, "keyword": keyword, "data": result}


# 所有工具列表，供 graph 使用
TOOLS_LIST = [
    tianyancha_query,
    search_company,
    assess_risk,
    check_alert,
    get_watchlist,
    add_to_watchlist,
    remove_from_watchlist,
    esg_assessment,
    contagion_analysis,
    sentiment_analysis,
    predict_risk,
    macro_risk,
    find_alternatives,
    scenario_simulate,
    check_sanctions,
    knowledge_search,
    generate_report,
    analyze_trend,
    compare_companies,
    query_financials,
    manage_scheduled_report,
]
