from app.schemas import RiskCalculateRequest, RiskCalculateResponse


def calculate_risk(request: RiskCalculateRequest) -> RiskCalculateResponse:
    score = 0

    # financial risk
    if request.financial:
        if request.financial.debt_ratio > 0.7:
            score += 20
        if request.financial.cash_flow < 0:
            score += 20
        if request.net_profit_declining:
            score += 15
        if request.revenue_declining:
            score += 15

    # judicial risk
    if request.risk.executed_count > 0:
        score += 25
    if request.dishonesty_count > 0:
        score += 30
    if request.major_lawsuit:
        score += 20

    # operational risk
    if request.risk.abnormal_operation_count > 0:
        score += 10
    if request.risk.administrative_penalty_count > 0:
        score += 10
    if request.legal_person_change_frequent:
        score += 10

    score = min(score, 100)

    if score <= 30:
        level = "低风险"
    elif score <= 60:
        level = "中风险"
    else:
        level = "高风险"

    return RiskCalculateResponse(risk_score=score, risk_level=level)
