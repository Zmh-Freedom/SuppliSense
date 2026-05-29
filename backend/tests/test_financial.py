from app.schemas.company import CompanyProfile
from app.schemas.financial import FinancialMetrics


def test_get_financial_metrics_listed(client, monkeypatch):
    mock_profile = CompanyProfile(
        company_name="测试上市公司",
        legal_person="张三",
        registered_capital="1亿人民币",
        establish_time="2005-01-01",
        is_listed=True,
    )
    mock_metrics = FinancialMetrics(
        revenue_growth=0.15,
        net_profit_growth=0.12,
        debt_ratio=0.45,
        cash_flow=200000000,
    )

    monkeypatch.setattr(
        "app.services.financial_service.get_baseinfo", lambda name: mock_profile
    )
    monkeypatch.setattr(
        "app.services.financial_service.repo_get_metrics", lambda name: mock_metrics
    )

    resp = client.get("/financial/metrics", params={"company_name": "测试上市公司"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["debt_ratio"] == 0.45
    assert data["cash_flow"] == 200000000


def test_get_financial_metrics_not_listed(client, monkeypatch):
    mock_profile = CompanyProfile(
        company_name="测试非上市公司",
        legal_person="李四",
        registered_capital="500万人民币",
        establish_time="2020-01-01",
        is_listed=False,
    )

    monkeypatch.setattr(
        "app.services.financial_service.get_baseinfo", lambda name: mock_profile
    )

    resp = client.get("/financial/metrics", params={"company_name": "测试非上市公司"})
    assert resp.status_code == 404


def test_get_financial_metrics_not_found(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.financial_service.get_baseinfo", lambda name: None
    )

    resp = client.get("/financial/metrics", params={"company_name": "不存在的公司"})
    assert resp.status_code == 404
