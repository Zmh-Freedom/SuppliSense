from fastapi import HTTPException

from app.repositories.company_repo import get_risk_info, get_risk_indicators
from app.repositories.financial_repo import get_financial_metrics
from app.schemas import RiskCalculateRequest, RiskCalculateResponse, RiskAssessRequest
from app.services.alert_service import save_snapshot
from app.services.company_service import get_company_profile


def assess_risk(request: RiskAssessRequest) -> RiskCalculateResponse:
    name = request.company_name

    try:
        profile = get_company_profile(name)
    except HTTPException as e:
        raise e

    risk = get_risk_info(name)
    indicators = get_risk_indicators(name)
    financial = get_financial_metrics(name)

    # check if in watchlist
    from app.services.alert_service import get_watchlist
    in_watchlist = name in get_watchlist()

    req = RiskCalculateRequest(
        company=profile,
        risk=risk,
        financial=financial,
        dishonesty_count=indicators["dishonesty_count"],
        major_lawsuit=indicators["major_lawsuit"],
        legal_person_change_frequent=indicators["legal_person_change_frequent"],
        net_profit_declining=indicators.get("net_profit_declining", False),
        revenue_declining=indicators.get("revenue_declining", False),
        guarantee_count=indicators["guarantee_count"],
        pledge_count=indicators["pledge_count"],
        bankruptcy_count=indicators["bankruptcy_count"],
        env_penalty_count=indicators["env_penalty_count"],
    )
    score, breakdown = _calc_score(req)
    level = _score_to_level(score)

    risk_detail = {
        "lawsuit_count": risk.lawsuit_count,
        "executed_count": indicators["executed_count"] or risk.executed_count,
        "dishonesty_count": indicators["dishonesty_count"],
        "major_lawsuit": indicators["major_lawsuit"],
        "abnormal_operation_count": risk.abnormal_operation_count,
        "administrative_penalty_count": risk.administrative_penalty_count,
        "legal_person_change_frequent": indicators["legal_person_change_frequent"],
        "guarantee_count": indicators["guarantee_count"],
        "pledge_count": indicators["pledge_count"],
        "bankruptcy_count": indicators["bankruptcy_count"],
        "env_penalty_count": indicators["env_penalty_count"],
    }

    response = RiskCalculateResponse(
        risk_score=score,
        risk_level=level,
        financial=financial,
        risk_detail=risk_detail,
        score_breakdown=breakdown,
    )
    # add watchlist status
    response.risk_detail["in_watchlist"] = in_watchlist
    save_snapshot(name, response)
    return response


def calculate_risk(request: RiskCalculateRequest) -> RiskCalculateResponse:
    score, breakdown = _calc_score(request)
    level = _score_to_level(score)
    return RiskCalculateResponse(risk_score=score, risk_level=level, score_breakdown=breakdown)


def _calc_score(req: RiskCalculateRequest) -> tuple[int, dict]:
    risk = req.risk
    fin = req.financial

    def clamp(v, lo, hi):
        return max(lo, min(hi, v))

    breakdown = {}

    # ---- financial ----
    fin_items = {}
    if fin:
        if fin.debt_ratio > 0.4:
            pts = round(clamp((fin.debt_ratio - 0.4) / 0.5 * 15, 0, 15), 1)
            fin_items["资产负债率"] = f"{pts}分 (当前{fin.debt_ratio*100:.1f}%)"
        if fin.cash_flow < 0:
            fin_items["现金流为负"] = f"8分 (每股{fin.cash_flow:.2f}元)"
        if fin.revenue_growth < 0:
            pts = round(clamp(3 + abs(fin.revenue_growth) * 20, 0, 7), 1)
            fin_items["营收下降"] = f"{pts}分 (增长率{fin.revenue_growth*100:.1f}%)"
        if fin.net_profit_growth < 0:
            pts = round(clamp(3 + abs(fin.net_profit_growth) * 20, 0, 7), 1)
            fin_items["净利下降"] = f"{pts}分 (增长率{fin.net_profit_growth*100:.1f}%)"
        # new: liquidity
        if fin.current_ratio > 0 and fin.current_ratio < 1.0:
            pts = round(clamp((1.0 - fin.current_ratio) * 10, 0, 6), 1)
            fin_items["流动比率过低"] = f"{pts}分 (当前{fin.current_ratio:.2f})"
        if fin.quick_ratio > 0 and fin.quick_ratio < 0.8:
            pts = round(clamp((0.8 - fin.quick_ratio) * 12, 0, 5), 1)
            fin_items["速动比率过低"] = f"{pts}分 (当前{fin.quick_ratio:.2f})"
        # new: profitability
        if fin.roe > 0 and fin.roe < 0.05:
            pts = 3
            fin_items["ROE偏低"] = f"{pts}分 ({fin.roe*100:.1f}%)"
        # new: earnings quality
        if fin.recurring_profit_ratio > 0 and fin.recurring_profit_ratio < 0.7:
            pts = round(clamp((0.7 - fin.recurring_profit_ratio) * 10, 0, 5), 1)
            fin_items["利润含金量低"] = f"{pts}分 (扣非占比{fin.recurring_profit_ratio*100:.1f}%)"
        # new: trends
        if fin.revenue_trend < -0.02:
            pts = 4
            fin_items["营收持续下滑"] = f"{pts}分 (3年趋势)"
        if fin.debt_trend > 0.02:
            pts = 3
            fin_items["负债率持续上升"] = f"{pts}分 (3年趋势)"
        if fin.ar_turnover_days > 180:
            pts = round(clamp((fin.ar_turnover_days - 180) / 180 * 4, 0, 4), 1)
            fin_items["应收款周转慢"] = f"{pts}分 ({fin.ar_turnover_days:.0f}天)"

    fin_score = sum(float(v.split("分")[0]) for v in fin_items.values())
    breakdown["财务风险"] = {"总分": round(fin_score, 1), "明细": fin_items}

    # ---- judicial ----
    jud_items = {}
    lawsuit_pts = round(clamp(risk.lawsuit_count * 0.3, 0, 10), 1)
    if lawsuit_pts > 0:
        jud_items["诉讼"] = f"{lawsuit_pts}分 ({risk.lawsuit_count}起)"
    exec_pts = round(clamp(risk.executed_count * 5, 0, 20), 1)
    if exec_pts > 0:
        jud_items["被执行"] = f"{exec_pts}分 ({risk.executed_count}条)"
    if req.dishonesty_count > 0:
        jud_items["失信"] = f"25分 ({req.dishonesty_count}条)"
    if req.major_lawsuit:
        jud_items["重大诉讼"] = "10分"
    guarantee_pts = round(clamp(req.guarantee_count * 0.005, 0, 5), 1)
    if guarantee_pts > 0:
        jud_items["对外担保"] = f"{guarantee_pts}分 ({req.guarantee_count}次)"
    pledge_pts = round(clamp(req.pledge_count * 0.3, 0, 4), 1)
    if pledge_pts > 0:
        jud_items["股权质押"] = f"{pledge_pts}分 ({req.pledge_count}次)"

    jud_score = sum(float(v.split("分")[0]) for v in jud_items.values())
    breakdown["司法风险"] = {"总分": round(jud_score, 1), "明细": jud_items}

    # ---- operational ----
    op_items = {}
    abnormal_pts = round(clamp(risk.abnormal_operation_count * 3, 0, 12), 1)
    if abnormal_pts > 0:
        op_items["经营异常"] = f"{abnormal_pts}分 ({risk.abnormal_operation_count}次)"
    penalty_pts = round(clamp(risk.administrative_penalty_count * 2, 0, 10), 1)
    if penalty_pts > 0:
        op_items["行政处罚"] = f"{penalty_pts}分 ({risk.administrative_penalty_count}条)"
    if req.legal_person_change_frequent:
        op_items["法人频繁变更"] = "5分"
    bankrupt_pts = round(clamp(req.bankruptcy_count * 3, 0, 9), 1)
    if bankrupt_pts > 0:
        op_items["破产/清算"] = f"{bankrupt_pts}分 ({req.bankruptcy_count}次)"
    env_pts = round(clamp(req.env_penalty_count * 2, 0, 5), 1)
    if env_pts > 0:
        op_items["环保处罚"] = f"{env_pts}分 ({req.env_penalty_count}条)"

    op_score = sum(float(v.split("分")[0]) for v in op_items.values())
    breakdown["经营风险"] = {"总分": round(op_score, 1), "明细": op_items}

    total = int(fin_score + jud_score + op_score)
    breakdown["总计"] = min(total, 100)
    if total > 100:
        breakdown["说明"] = "实际总分超过100，已封顶"

    return min(total, 100), breakdown


def _score_to_level(score: int) -> str:
    if score <= 30:
        return "低风险"
    elif score <= 60:
        return "中风险"
    else:
        return "高风险"
