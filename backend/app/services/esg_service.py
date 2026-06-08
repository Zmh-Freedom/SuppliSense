"""
ESG 风险评分服务。

基于天眼查数据 + 财务指标计算 ESG 三维评分：
  - Environmental (E): 环保处罚、碳排放相关
  - Social (S): 劳动纠纷、社保缴纳、行政处罚
  - Governance (G): 公司治理、司法风险、经营稳定性

评分范围：0-100，分数越高风险越大。
"""

from datetime import datetime, timezone

from app.db.mongo import get_db
from app.repositories.company_repo import get_baseinfo, get_risk_indicators
from app.repositories.financial_repo import get_financial_metrics


def assess_esg(company_name: str) -> dict | None:
    """评估企业 ESG 风险。返回三维评分 + 总分。"""
    profile = get_baseinfo(company_name)
    if not profile:
        return None

    indicators = get_risk_indicators(company_name)
    fin = get_financial_metrics(company_name)

    # ---- Environmental (E) ----
    e_score = 0.0
    e_detail: list[dict] = []

    env_count = indicators.get("env_penalty_count", 0)
    if env_count >= 5:
        e_score += 40
        e_detail.append({"item": "环保处罚", "value": f"{env_count}条", "score": 40, "level": "high"})
    elif env_count >= 1:
        e_score += 20
        e_detail.append({"item": "环保处罚", "value": f"{env_count}条", "score": 20, "level": "medium"})
    else:
        e_detail.append({"item": "环保处罚", "value": "无", "score": 0, "level": "low"})

    # E: pollution-related from financials
    if fin:
        # heavy industry: high energy consumption implies higher environmental risk
        if fin.debt_ratio > 0.7:
            e_score += 10
            e_detail.append({"item": "高负债（隐含环保投入压力）", "value": f"{fin.debt_ratio*100:.1f}%", "score": 10, "level": "low"})

    e_level = _level(e_score, 30, 60)

    # ---- Social (S) ----
    s_score = 0.0
    s_detail: list[dict] = []

    # labor disputes indicator
    lawsuit_count = indicators.get("lawsuit_count", 0)
    if lawsuit_count >= 50:
        s_score += 40
        s_detail.append({"item": "劳动/社会纠纷", "value": f"诉讼{ lawsuit_count}条", "score": 40, "level": "high"})
    elif lawsuit_count >= 10:
        s_score += 20
        s_detail.append({"item": "劳动/社会纠纷", "value": f"诉讼{lawsuit_count}条", "score": 20, "level": "medium"})
    elif lawsuit_count > 0:
        s_score += 5
        s_detail.append({"item": "劳动/社会纠纷", "value": f"诉讼{lawsuit_count}条", "score": 5, "level": "low"})
    else:
        s_detail.append({"item": "劳动/社会纠纷", "value": "无", "score": 0, "level": "low"})

    # administrative penalty
    admin_count = indicators.get("administrative_penalty_count", 0) if hasattr(indicators, '__getitem__') else 0
    admin_count = _get_admin_count(company_name)
    if admin_count >= 5:
        s_score += 30
        s_detail.append({"item": "行政处罚", "value": f"{admin_count}条", "score": 30, "level": "high"})
    elif admin_count >= 1:
        s_score += 15
        s_detail.append({"item": "行政处罚", "value": f"{admin_count}条", "score": 15, "level": "medium"})
    else:
        s_detail.append({"item": "行政处罚", "value": "无", "score": 0, "level": "low"})

    s_level = _level(s_score, 30, 60)

    # ---- Governance (G) ----
    g_score = 0.0
    g_detail: list[dict] = []

    # legal person change frequency
    if indicators.get("legal_person_change_frequent"):
        g_score += 25
        g_detail.append({"item": "法人频繁变更", "value": "是", "score": 25, "level": "high"})
    else:
        g_detail.append({"item": "法人变更", "value": "正常", "score": 0, "level": "low"})

    # dishonesty / executed
    dishonesty = indicators.get("dishonesty_count", 0)
    executed = indicators.get("executed_count", 0)
    if dishonesty > 0 or executed > 0:
        g_score += 30
        g_detail.append({"item": "失信/被执行", "value": f"失信{dishonesty}/被执行{executed}", "score": 30, "level": "high"})
    else:
        g_detail.append({"item": "失信/被执行", "value": "无", "score": 0, "level": "low"})

    # bankruptcy
    bankruptcy = indicators.get("bankruptcy_count", 0)
    if bankruptcy > 0:
        g_score += 35
        g_detail.append({"item": "破产/清算", "value": f"{bankruptcy}条", "score": 35, "level": "high"})
    else:
        g_detail.append({"item": "破产/清算", "value": "无", "score": 0, "level": "low"})

    # guarantee / pledge (governance risk)
    guarantee = indicators.get("guarantee_count", 0)
    pledge = indicators.get("pledge_count", 0)
    if guarantee + pledge >= 10:
        g_score += 20
        g_detail.append({"item": "对外担保/股权质押", "value": f"担保{guarantee}/质押{pledge}", "score": 20, "level": "medium"})
    elif guarantee + pledge > 0:
        g_score += 10
        g_detail.append({"item": "对外担保/股权质押", "value": f"担保{guarantee}/质押{pledge}", "score": 10, "level": "low"})
    else:
        g_detail.append({"item": "对外担保/股权质押", "value": "无", "score": 0, "level": "low"})

    g_level = _level(g_score, 30, 60)

    # ---- total ----
    total_score = round(e_score + s_score + g_score, 1)
    total_level = _level(total_score, 60, 120)

    return {
        "company_name": company_name,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "total_score": min(total_score, 100),
        "total_level": total_level,
        "environmental": {"score": round(e_score, 1), "level": e_level, "detail": e_detail},
        "social": {"score": round(s_score, 1), "level": s_level, "detail": s_detail},
        "governance": {"score": round(g_score, 1), "level": g_level, "detail": g_detail},
    }


def _level(score: float, mid: float, high: float) -> str:
    if score >= high:
        return "高风险"
    elif score >= mid:
        return "中风险"
    return "低风险"


def _get_admin_count(company_name: str) -> int:
    """Get administrative penalty count from MongoDB."""
    db = get_db()
    doc = db["punishmentInfo"].find_one({"name": company_name})
    if doc:
        result = (doc.get("items") or {}).get("result") or {}
        return result.get("total", 0) if isinstance(result, dict) else 0
    return 0


def assess_all_esg() -> list[dict]:
    """评估所有监控企业的 ESG 风险。"""
    db = get_db()
    companies = [doc["company_name"] for doc in db["watchlist"].find()]
    results = []
    for name in companies:
        r = assess_esg(name)
        if r:
            results.append(r)
    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results
