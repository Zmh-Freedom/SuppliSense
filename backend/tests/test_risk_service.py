from app.domains.risk.service import _clamp, _score_to_level, calculate_company_risk_preview
from app.domains.risk.repo_financial import _parse_float
from app.schemas.company import CompanyProfile
from app.schemas.risk import RiskInfo


class TestClamp:
    def test_keeps_value_in_range(self):
        assert _clamp(5, 0, 10) == 5

    def test_clamps_high(self):
        assert _clamp(15, 0, 10) == 10

    def test_clamps_low(self):
        assert _clamp(-5, 0, 10) == 0

    def test_boundary_equals_lo(self):
        assert _clamp(0, 0, 10) == 0

    def test_boundary_equals_hi(self):
        assert _clamp(10, 0, 10) == 10


class TestScoreToLevel:
    def test_low_risk(self):
        assert _score_to_level(20) == "低风险"

    def test_medium_risk(self):
        assert _score_to_level(45) == "中风险"

    def test_high_risk(self):
        assert _score_to_level(75) == "高风险"


class TestParseFloat:
    def test_plain_number(self):
        assert _parse_float("123.45") == 123.45

    def test_with_yi(self):
        assert _parse_float("3亿") == 300000000.0

    def test_with_wan(self):
        assert _parse_float("1.5万") == 15000.0

    def test_empty_string(self):
        assert _parse_float("") == 0.0

    def test_none_value(self):
        assert _parse_float(None) == 0.0


def test_risk_preview_reuses_v2_scoring_without_snapshot_or_soft_risk(monkeypatch):
    profile = CompanyProfile(
        company_name="供应商甲",
        legal_person="测试法人",
        registered_capital="100万人民币",
        establish_time="2020-01-01",
        industry="制造业",
        is_listed=False,
    )
    risk = RiskInfo(
        lawsuit_count=6,
        executed_count=0,
        abnormal_operation_count=0,
        administrative_penalty_count=0,
    )
    indicators = {
        "dishonesty_count": 0,
        "major_lawsuit": True,
        "legal_person_change_frequent": False,
        "net_profit_declining": False,
        "revenue_declining": False,
        "guarantee_count": 0,
        "pledge_count": 0,
        "bankruptcy_count": 0,
        "env_penalty_count": 0,
        "executed_count": 0,
    }
    monkeypatch.setattr("app.domains.risk.service.get_baseinfo", lambda name: profile)
    monkeypatch.setattr("app.domains.risk.service.get_risk_info", lambda name: risk)
    monkeypatch.setattr("app.domains.risk.service.get_risk_indicators", lambda name: indicators)
    monkeypatch.setattr("app.domains.risk.service.get_financial_metrics", lambda name: None)
    monkeypatch.setattr("app.domains.risk.service.get_recent_lawsuits", lambda name, years: 2)
    monkeypatch.setattr("app.domains.risk.soft_risk.score_soft_risks", lambda name: (_ for _ in ()).throw(AssertionError("must not refresh soft risk")))

    result = calculate_company_risk_preview("供应商甲")

    assert result is not None
    assert result.risk_score > 0
    assert result.risk_detail["lawsuit_count"] == 6
    assert result.risk_detail["data_coverage"]["assessment_status"] == "partial"
