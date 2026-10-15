import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException, Request

from app.domains.risk import api_risk
from app.domains.alert import api as alert_api
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

    audit_calls: list[dict] = []

    def fake_log_action(**kwargs) -> None:
        audit_calls.append(kwargs)

    monkeypatch.setattr(api_risk, "log_action", fake_log_action)

    result = asyncio.run(
        api_risk.risk_assess(
            RiskAssessRequest(company_name="首次评估企业有限公司"),
            BackgroundTasks(),
            Request({"type": "http", "method": "POST", "path": "/risk/assess", "headers": [], "client": ("127.0.0.1", 1234)}),
            SimpleNamespace(id="user-1"),
        )
    )

    assert result is fresh
    assert result.cached_at is not None
    assert result.cache_age_hours == 0
    assert result.is_stale is False
    assert audit_calls[0]["action"] == "assess_risk_requested"
    assert audit_calls[0]["required"] is True
    audit_call = audit_calls[1]
    assert audit_call["details"] == {
        "risk_score": 37,
        "risk_level": "中风险",
    }
    assert audit_call["user_id"] == "user-1"
    assert audit_call["ip_address"] == "127.0.0.1"


def test_uncached_risk_assessment_stops_when_required_audit_fails(monkeypatch):
    from app.domains.auth.audit import AuditUnavailable

    db = MagicMock()
    db["alert_snapshots"].find_one.return_value = None
    monkeypatch.setattr("app.db.mongo.get_db", lambda: db)
    assessed: list[str] = []
    monkeypatch.setattr(api_risk, "assess_risk", lambda _request: assessed.append("assessed"))
    monkeypatch.setattr(
        api_risk, "log_action", lambda **_kwargs: (_ for _ in ()).throw(AuditUnavailable("审计服务暂不可用"))
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(api_risk.risk_assess(
            RiskAssessRequest(company_name="企业"), BackgroundTasks(),
            Request({"type": "http", "method": "POST", "path": "/risk/assess", "headers": []}),
            SimpleNamespace(id="user-1"),
        ))

    assert raised.value.status_code == 503
    assert assessed == []


def test_background_risk_refresh_failure_keeps_original_error(monkeypatch):
    monkeypatch.setattr(
        api_risk,
        "assess_risk",
        lambda _request: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )

    api_risk._refresh_assessment("甲公司")


def test_alert_snapshots_returns_serialized_version_history(monkeypatch):
    monkeypatch.setattr(
        alert_api,
        "get_snapshot_history",
        lambda company_name, limit: [{
            "_id": "mongo-id",
            "snapshot_id": "snapshot-2",
            "snapshot_version": 2,
            "company_name": company_name,
            "checked_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        }],
    )

    result = asyncio.run(alert_api.alert_snapshots("  示例企业有限公司  ", limit=20))

    assert result["company_name"] == "示例企业有限公司"
    assert result["count"] == 1
    assert result["snapshots"][0]["_id"] == "mongo-id"
    assert result["snapshots"][0]["checked_at"] == "2026-09-01T00:00:00+00:00"
