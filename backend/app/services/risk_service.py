from fastapi import HTTPException

from app.repositories.company_repo import get_risk_info, get_risk_indicators
from app.repositories.financial_repo import get_financial_metrics
from app.schemas import RiskCalculateRequest, RiskCalculateResponse, RiskAssessRequest
from app.services.alert_service import save_snapshot
from app.services.company_service import get_company_profile

# ---- 归一化参数：每个维度上限 25 分，四维总计 0-100 ----
# 归一化基准值（各维度理论最大值），用于将原始分映射到 0-25
FIN_NORM = 70   # 财务维度 11 个指标的理论上限
JUD_NORM = 75   # 司法维度 6 个指标的理论上限
OP_NORM = 45    # 经营维度 5 个指标的理论上限
SOFT_NORM = 60  # 软指标 4 个维度（LLM 评估）的理论上限


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
        is_listed=profile.is_listed,
    )
    # add watchlist status
    response.risk_detail["in_watchlist"] = in_watchlist
    save_snapshot(name, response)
    return response


def calculate_risk(request: RiskCalculateRequest) -> RiskCalculateResponse:
    score, breakdown = _calc_score(request)
    level = _score_to_level(score)
    return RiskCalculateResponse(risk_score=score, risk_level=level, score_breakdown=breakdown)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _normalize(raw: float, norm_base: float) -> float:
    """将原始分数映射到 0-25 区间。"""
    return round(min(raw / norm_base * 25, 25), 1)


def _calc_score(req: RiskCalculateRequest) -> tuple[int, dict]:
    risk = req.risk
    fin = req.financial

    breakdown = {}

    # ==================== 财务风险 (0-25) ====================
    fin_items = {}
    fin_raw = 0
    if fin:
        if fin.debt_ratio > 0.4:
            pts = _clamp((fin.debt_ratio - 0.4) / 0.5 * 15, 0, 15)
            fin_items["资产负债率"] = f"{pts:.1f}分 (当前{fin.debt_ratio*100:.1f}%)"
            fin_raw += pts
        if fin.cash_flow < 0:
            fin_items["现金流为负"] = f"8分 (每股{fin.cash_flow:.2f}元)"
            fin_raw += 8
        if fin.revenue_growth < 0:
            pts = _clamp(3 + abs(fin.revenue_growth) * 20, 0, 7)
            fin_items["营收下降"] = f"{pts:.1f}分 (增长率{fin.revenue_growth*100:.1f}%)"
            fin_raw += pts
        if fin.net_profit_growth < 0:
            pts = _clamp(3 + abs(fin.net_profit_growth) * 20, 0, 7)
            fin_items["净利下降"] = f"{pts:.1f}分 (增长率{fin.net_profit_growth*100:.1f}%)"
            fin_raw += pts
        if fin.current_ratio > 0 and fin.current_ratio < 1.0:
            pts = _clamp((1.0 - fin.current_ratio) * 10, 0, 6)
            fin_items["流动比率过低"] = f"{pts:.1f}分 (当前{fin.current_ratio:.2f})"
            fin_raw += pts
        if fin.quick_ratio > 0 and fin.quick_ratio < 0.8:
            pts = _clamp((0.8 - fin.quick_ratio) * 12, 0, 5)
            fin_items["速动比率过低"] = f"{pts:.1f}分 (当前{fin.quick_ratio:.2f})"
            fin_raw += pts
        if fin.roe > 0 and fin.roe < 0.05:
            fin_items["ROE偏低"] = f"3分 ({fin.roe*100:.1f}%)"
            fin_raw += 3
        if fin.recurring_profit_ratio > 0 and fin.recurring_profit_ratio < 0.7:
            pts = _clamp((0.7 - fin.recurring_profit_ratio) * 10, 0, 5)
            fin_items["利润含金量低"] = f"{pts:.1f}分 (扣非占比{fin.recurring_profit_ratio*100:.1f}%)"
            fin_raw += pts
        if fin.revenue_trend < -0.02:
            fin_items["营收持续下滑"] = "4分 (3年趋势)"
            fin_raw += 4
        if fin.debt_trend > 0.02:
            fin_items["负债率持续上升"] = "3分 (3年趋势)"
            fin_raw += 3
        if fin.ar_turnover_days > 180:
            pts = _clamp((fin.ar_turnover_days - 180) / 180 * 4, 0, 4)
            fin_items["应收款周转慢"] = f"{pts:.1f}分 ({fin.ar_turnover_days:.0f}天)"
            fin_raw += pts

    fin_norm = _normalize(fin_raw, FIN_NORM)
    breakdown["财务风险"] = {"原始分": round(fin_raw, 1), "归一化": fin_norm, "明细": fin_items}

    # ==================== 司法风险 (0-25) ====================
    jud_items = {}
    jud_raw = 0
    lawsuit_pts = _clamp(risk.lawsuit_count * 0.3, 0, 10)
    if lawsuit_pts > 0:
        jud_items["诉讼"] = f"{lawsuit_pts:.1f}分 ({risk.lawsuit_count}起)"
        jud_raw += lawsuit_pts
    exec_pts = _clamp(risk.executed_count * 5, 0, 20)
    if exec_pts > 0:
        jud_items["被执行"] = f"{exec_pts:.1f}分 ({risk.executed_count}条)"
        jud_raw += exec_pts
    if req.dishonesty_count > 0:
        if req.dishonesty_count <= 1:
            dishonesty_pts = 10
        elif req.dishonesty_count <= 5:
            dishonesty_pts = 20
        else:
            dishonesty_pts = 25
        jud_items["失信"] = f"{dishonesty_pts}分 ({req.dishonesty_count}条)"
        jud_raw += dishonesty_pts
    if req.major_lawsuit:
        jud_items["重大诉讼"] = "10分"
        jud_raw += 10
    guarantee_pts = _clamp(req.guarantee_count * 0.005, 0, 5)
    if guarantee_pts > 0:
        jud_items["对外担保"] = f"{guarantee_pts:.1f}分 ({req.guarantee_count}次)"
        jud_raw += guarantee_pts
    pledge_pts = _clamp(req.pledge_count * 0.3, 0, 4)
    if pledge_pts > 0:
        jud_items["股权质押"] = f"{pledge_pts:.1f}分 ({req.pledge_count}次)"
        jud_raw += pledge_pts

    jud_norm = _normalize(jud_raw, JUD_NORM)
    breakdown["司法风险"] = {"原始分": round(jud_raw, 1), "归一化": jud_norm, "明细": jud_items}

    # ==================== 经营风险 (0-25) ====================
    op_items = {}
    op_raw = 0
    abnormal_pts = _clamp(risk.abnormal_operation_count * 3, 0, 12)
    if abnormal_pts > 0:
        op_items["经营异常"] = f"{abnormal_pts:.1f}分 ({risk.abnormal_operation_count}次)"
        op_raw += abnormal_pts
    penalty_pts = _clamp(risk.administrative_penalty_count * 2, 0, 10)
    if penalty_pts > 0:
        op_items["行政处罚"] = f"{penalty_pts:.1f}分 ({risk.administrative_penalty_count}条)"
        op_raw += penalty_pts
    if req.legal_person_change_frequent:
        op_items["法人频繁变更"] = "5分"
        op_raw += 5
    bankrupt_pts = _clamp(req.bankruptcy_count * 3, 0, 9)
    if bankrupt_pts > 0:
        op_items["破产/清算"] = f"{bankrupt_pts:.1f}分 ({req.bankruptcy_count}次)"
        op_raw += bankrupt_pts
    env_pts = _clamp(req.env_penalty_count * 2, 0, 5)
    if env_pts > 0:
        op_items["环保处罚"] = f"{env_pts:.1f}分 ({req.env_penalty_count}条)"
        op_raw += env_pts

    op_norm = _normalize(op_raw, OP_NORM)
    breakdown["经营风险"] = {"原始分": round(op_raw, 1), "归一化": op_norm, "明细": op_items}

    # ==================== 软指标 (0-25) ====================
    from app.services.soft_risk import score_soft_risks

    soft = score_soft_risks(req.company.company_name)
    soft_items = {}
    soft_raw = 0
    for dim in ["舆情风险", "ESG风险", "宏观风险", "管理风险"]:
        d = soft.get("dimensions", {}).get(dim, {})
        s = int(d.get("score", 0))
        r = d.get("reason", "")
        soft_items[dim] = f"{s}分 · {r}" if r else f"{s}分"
        soft_raw += s
    if soft.get("summary"):
        soft_items["总结"] = soft["summary"]

    soft_norm = _normalize(soft_raw, SOFT_NORM)
    breakdown["软指标"] = {"原始分": soft_raw, "归一化": soft_norm, "明细": soft_items}

    # ==================== 总分 ====================
    total = round(fin_norm + jud_norm + op_norm + soft_norm)
    breakdown["总计"] = total
    breakdown["权重"] = "财务 25% + 司法 25% + 经营 25% + 软指标 25%"

    return total, breakdown


def _score_to_level(score: int) -> str:
    if score <= 30:
        return "低风险"
    elif score <= 60:
        return "中风险"
    else:
        return "高风险"
