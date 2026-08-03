import asyncio
from unittest.mock import MagicMock

from fastapi import BackgroundTasks

from app.domains.risk import api_risk
from app.schemas import RiskAssessRequest, RiskCalculateResponse


def test_uncached_risk_assessment_audits_pydantic_response(monkeypatch):
    db = MagicMock()
    db["alert_snapshots"].find_one.return_value = None
    monkeypatch.setattr("app.db.mongo.get_db", lambda: db)

    fresh = RiskCalculateResponse(
        risk_score=37,
        risk_level="中风险",
        risk_detail={"lawsuit_count": 1},
    )
    monkeypatch.setattr(api_risk, "assess_risk", lambda request: fresh)

    audit_call: dict = {}

    def fake_log_action(**kwargs) -> None:
        audit_call.update(kwargs)

    monkeypatch.setattr("app.domains.auth.audit.log_action", fake_log_action)

    result = asyncio.run(
        api_risk.risk_assess(
            RiskAssessRequest(company_name="首次评估企业有限公司"),
            BackgroundTasks(),
        )
    )

    assert result is fresh
    assert result.cached_at is not None
    assert result.cache_age_hours == 0
    assert result.is_stale is False
    assert audit_call["details"] == {
        "risk_score": 37,
        "risk_level": "中风险",
    }
