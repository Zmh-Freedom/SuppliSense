"""财务端点认证测试。"""


def test_financial_metrics_requires_auth(client):
    resp = client.get("/api/v1/financial/metrics", params={"company_name": "测试公司"})
    assert resp.status_code == 401
