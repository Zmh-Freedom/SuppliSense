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


# 所有工具列表，供 graph 使用
TOOLS_LIST = [
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
]
