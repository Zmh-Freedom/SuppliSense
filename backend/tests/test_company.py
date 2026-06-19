"""企业信息端点认证测试。"""


def test_company_profile_requires_auth(client):
    resp = client.get("/api/v1/company/profile", params={"company_name": "测试公司"})
    assert resp.status_code == 401


def test_company_risk_requires_auth(client):
    resp = client.get("/api/v1/company/risk", params={"company_name": "测试公司"})
    assert resp.status_code == 401


def test_company_search_requires_auth(client):
    resp = client.get("/api/v1/company/search", params={"q": "测试"})
    assert resp.status_code == 401
