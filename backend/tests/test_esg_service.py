from app.domains.risk.esg_service import assess_esg


def test_esg_marks_result_insufficient_when_core_dimensions_are_uncovered(monkeypatch):
    class Collection:
        def __init__(self, doc=None):
            self.doc = doc

        def find_one(self, query):
            return self.doc

    db = {
        "riskInfo": Collection(),
        "lawSuit": Collection({"name": "供应商甲"}),
        "punishmentInfo": Collection(),
    }
    monkeypatch.setattr("app.domains.risk.esg_service.get_db", lambda: db)
    monkeypatch.setattr("app.domains.risk.esg_service.get_baseinfo", lambda name: {"name": name})
    monkeypatch.setattr("app.domains.risk.esg_service.get_risk_indicators", lambda name: {
        "env_penalty_count": 0,
        "lawsuit_count": 1,
        "legal_person_change_frequent": False,
        "dishonesty_count": 0,
        "executed_count": 0,
        "bankruptcy_count": 0,
        "guarantee_count": 0,
        "pledge_count": 0,
    })
    monkeypatch.setattr("app.domains.risk.esg_service.get_financial_metrics", lambda name: None)
    monkeypatch.setattr("app.domains.risk.esg_service._get_admin_count", lambda name: 0)

    result = assess_esg("供应商甲")

    assert result is not None
    assert result["calculated_level"] == "低风险"
    assert result["total_level"] == "数据不足"
    assert result["assessment_status"] == "insufficient_data"
    assert result["data_coverage"]["coverage_ratio"] == 0.25
