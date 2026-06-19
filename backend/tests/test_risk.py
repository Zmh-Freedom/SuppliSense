"""风险计算端点测试。"""


def test_risk_calculate_requires_auth(client):
    """未认证请求应返回 401。"""
    resp = client.post("/api/v1/risk/calculate", json={
        "company": {"company_name": "测试公司", "legal_person": "王芳", "registered_capital": "2亿", "establish_time": "2005-01-10", "is_listed": True},
        "risk": {"lawsuit_count": 1, "executed_count": 0, "abnormal_operation_count": 0, "administrative_penalty_count": 0},
        "financial": {"revenue_growth": 0.15, "net_profit_growth": 0.12, "debt_ratio": 0.45, "cash_flow": 200000000},
        "dishonesty_count": 0, "major_lawsuit": False, "legal_person_change_frequent": False,
        "net_profit_declining": False, "revenue_declining": False,
    })
    assert resp.status_code == 401


def test_risk_assess_requires_auth(client):
    """未认证请求应返回 401。"""
    resp = client.post("/api/v1/risk/assess", json={"company_name": "测试公司"})
    assert resp.status_code == 401
