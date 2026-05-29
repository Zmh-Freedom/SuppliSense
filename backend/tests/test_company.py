from app.schemas.company import CompanyProfile
from app.schemas.risk import RiskInfo


def test_get_company_profile_found(client, monkeypatch):
    mock_profile = CompanyProfile(
        company_name="杭州海康威视数字技术股份有限公司",
        legal_person="陈宗年",
        registered_capital="93亿人民币",
        establish_time="2001-11-30",
        is_listed=True,
    )

    def mock_get_baseinfo(name):
        return mock_profile

    monkeypatch.setattr(
        "app.services.company_service.get_baseinfo", mock_get_baseinfo
    )
    monkeypatch.setattr(
        "app.repositories.company_repo.get_baseinfo", mock_get_baseinfo
    )

    resp = client.get(
        "/company/profile",
        params={"company_name": "杭州海康威视数字技术股份有限公司"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["company_name"] == "杭州海康威视数字技术股份有限公司"
    assert data["is_listed"] is True


def test_get_company_profile_not_found(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.company_service.get_baseinfo", lambda name: None
    )
    monkeypatch.setattr(
        "app.repositories.company_repo.get_baseinfo", lambda name: None
    )

    resp = client.get("/company/profile", params={"company_name": "不存在的公司"})
    assert resp.status_code == 404


def test_get_company_risk_found(client, monkeypatch):
    mock_risk = RiskInfo(
        lawsuit_count=5,
        executed_count=0,
        abnormal_operation_count=0,
        administrative_penalty_count=1,
    )

    monkeypatch.setattr(
        "app.services.company_service.get_risk_info", lambda name: mock_risk
    )
    monkeypatch.setattr(
        "app.repositories.company_repo.get_risk_info", lambda name: mock_risk
    )

    resp = client.get("/company/risk", params={"company_name": "测试公司"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["lawsuit_count"] == 5
    assert data["administrative_penalty_count"] == 1


def test_get_company_risk_not_found(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.company_service.get_risk_info", lambda name: None
    )
    monkeypatch.setattr(
        "app.repositories.company_repo.get_risk_info", lambda name: None
    )

    resp = client.get("/company/risk", params={"company_name": "不存在的公司"})
    assert resp.status_code == 404
