def test_risk_calculate_low_risk(client):
    payload = {
        "company": {"company_name": "测试低风险公司", "legal_person": "王芳", "registered_capital": "2亿", "establish_time": "2005-01-10", "is_listed": True},
        "risk": {"lawsuit_count": 1, "executed_count": 0, "abnormal_operation_count": 0, "administrative_penalty_count": 0},
        "financial": {"revenue_growth": 0.15, "net_profit_growth": 0.12, "debt_ratio": 0.45, "cash_flow": 200000000},
        "dishonesty_count": 0, "major_lawsuit": False, "legal_person_change_frequent": False,
        "net_profit_declining": False, "revenue_declining": False,
    }
    resp = client.post("/risk/calculate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    # debt=45% -> 1.5, lawsuit=1 -> 0.3
    assert data["risk_score"] == 1
    assert data["risk_level"] == "低风险"


def test_risk_calculate_high_risk(client):
    payload = {
        "company": {"company_name": "测试高风险", "legal_person": "刘强", "registered_capital": "10亿", "establish_time": "1998-11-05", "is_listed": True},
        "risk": {"lawsuit_count": 25, "executed_count": 5, "abnormal_operation_count": 2, "administrative_penalty_count": 4},
        "financial": {"revenue_growth": -0.20, "net_profit_growth": -0.35, "debt_ratio": 0.85, "cash_flow": -200000000},
        "dishonesty_count": 2, "major_lawsuit": True, "legal_person_change_frequent": True,
        "net_profit_declining": True, "revenue_declining": True,
    }
    resp = client.post("/risk/calculate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["risk_score"] == 100
    assert data["risk_level"] == "高风险"


def test_risk_calculate_medium_risk(client):
    payload = {
        "company": {"company_name": "测试中风险", "legal_person": "张伟", "registered_capital": "5000万", "establish_time": "2010-03-15", "is_listed": True},
        "risk": {"lawsuit_count": 3, "executed_count": 0, "abnormal_operation_count": 0, "administrative_penalty_count": 0},
        "financial": {"revenue_growth": -0.05, "net_profit_growth": -0.15, "debt_ratio": 0.78, "cash_flow": 10000000},
        "dishonesty_count": 0, "major_lawsuit": False, "legal_person_change_frequent": False,
        "net_profit_declining": True, "revenue_declining": False,
    }
    resp = client.post("/risk/calculate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    # debt 78%=11, rev -5%=4, profit -15%=6, lawsuit 3=0.9
    assert data["risk_score"] == 22
    assert data["risk_level"] == "低风险"


def test_risk_calculate_non_listed(client):
    payload = {
        "company": {"company_name": "测试非上市", "legal_person": "李明", "registered_capital": "1000万", "establish_time": "2018-07-20", "is_listed": False},
        "risk": {"lawsuit_count": 3, "executed_count": 0, "abnormal_operation_count": 0, "administrative_penalty_count": 1},
        "financial": None, "dishonesty_count": 0, "major_lawsuit": False,
        "legal_person_change_frequent": False, "net_profit_declining": False, "revenue_declining": False,
    }
    resp = client.post("/risk/calculate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    # lawsuit 3=0.9, penalty 1=2
    assert data["risk_score"] == 2
    assert data["risk_level"] == "低风险"


def test_risk_calculate_score_boundary(client):
    payload = {
        "company": {"company_name": "测试边界", "legal_person": "测试", "registered_capital": "100万", "establish_time": "2020-01-01", "is_listed": False},
        "risk": {"lawsuit_count": 0, "executed_count": 0, "abnormal_operation_count": 1, "administrative_penalty_count": 1},
        "financial": None, "dishonesty_count": 0, "major_lawsuit": True,
        "legal_person_change_frequent": False, "net_profit_declining": False, "revenue_declining": False,
    }
    resp = client.post("/risk/calculate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    # abnormal 1=3, penalty 1=2, major_lawsuit=10
    assert data["risk_score"] == 15
    assert data["risk_level"] == "低风险"


def test_continuous_scaling(client):
    """verify counts scale continuously, not binary"""
    payload = {
        "company": {"company_name": "c", "legal_person": "x", "registered_capital": "1", "establish_time": "2020-01-01", "is_listed": False},
        "risk": {"lawsuit_count": 10, "executed_count": 2, "abnormal_operation_count": 2, "administrative_penalty_count": 3},
        "financial": None, "dishonesty_count": 0, "major_lawsuit": False,
        "legal_person_change_frequent": False, "net_profit_declining": False, "revenue_declining": False,
    }
    resp = client.post("/risk/calculate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    # lawsuit 10=3, executed 2=10, abnormal 2=6, penalty 3=6
    assert data["risk_score"] == 25
