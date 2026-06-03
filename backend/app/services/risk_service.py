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
    score = _calc_score(req)
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
    )
    save_snapshot(name, response)
    return response


def calculate_risk(request: RiskCalculateRequest) -> RiskCalculateResponse:
    score = _calc_score(request)
    level = _score_to_level(score)
    return RiskCalculateResponse(risk_score=score, risk_level=level)


def _calc_score(req: RiskCalculateRequest) -> int:
    risk = req.risk
    fin = req.financial

    def clamp(v, lo, hi):
        return max(lo, min(hi, v))

    # ---- financial (max 35) ----
    fin_score = 0.0
    if fin:
        if fin.debt_ratio > 0.4:
            fin_score += clamp((fin.debt_ratio - 0.4) / 0.5 * 15, 0, 15)
        if fin.cash_flow < 0:
            fin_score += 8
        if fin.revenue_growth < 0:
            fin_score += clamp(3 + abs(fin.revenue_growth) * 20, 0, 7)
        if fin.net_profit_growth < 0:
            fin_score += clamp(3 + abs(fin.net_profit_growth) * 20, 0, 7)

    # ---- judicial (max 40) ----
    judicial_score = 0.0
    judicial_score += clamp(risk.lawsuit_count * 0.3, 0, 10)
    judicial_score += clamp(risk.executed_count * 5, 0, 20)
    if req.dishonesty_count > 0:
        judicial_score += 25
    if req.major_lawsuit:
        judicial_score += 10
    # new dimensions
    judicial_score += clamp(req.guarantee_count * 0.005, 0, 5)  # 对外担保
    judicial_score += clamp(req.pledge_count * 0.3, 0, 4)  # 股权质押

    # ---- operational (max 25) ----
    op_score = 0.0
    op_score += clamp(risk.abnormal_operation_count * 3, 0, 12)
    op_score += clamp(risk.administrative_penalty_count * 2, 0, 10)
    if req.legal_person_change_frequent:
        op_score += 5
    # new dimensions
    op_score += clamp(req.bankruptcy_count * 3, 0, 9)  # 破产/清算
    op_score += clamp(req.env_penalty_count * 2, 0, 5)  # 环保处罚

    total = int(fin_score + judicial_score + op_score)
    return min(total, 100)


def _score_to_level(score: int) -> str:
    if score <= 30:
        return "低风险"
    elif score <= 60:
        return "中风险"
    else:
        return "高风险"
