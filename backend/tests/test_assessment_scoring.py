"""Frozen scoring must not turn provider failure into safety points."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.domains.risk.assessment_scoring import score_frozen_inputs
from app.domains.risk.assessment_policy import current_policy
from app.schemas import RiskCalculateRequest
from app.schemas.company import CompanyProfile
from app.schemas.risk import RiskInfo


def inputs():
    request = RiskCalculateRequest(
        company=CompanyProfile(
            company_name="甲公司",
            legal_person="",
            registered_capital="",
            establish_time="",
            industry="制造业",
            is_listed=False,
        ),
        risk=RiskInfo(
            lawsuit_count=10,
            executed_count=0,
            abnormal_operation_count=0,
            administrative_penalty_count=0,
        ),
    )
    return {
        "request": request.model_dump(mode="json"),
        "recent_lawsuits": 10,
        "soft": {"status": "unavailable", "dimensions": {}},
    }


def evidence(dimension, status="available"):
    return {
        "evidence_id": dimension,
        "dimension": dimension,
        "status": status,
        "source": "fixture",
        "raw_payload_ref": "raw:" + dimension,
        "content_hash": "a" * 64,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "query_receipt": {"status": "success", "record_count": 1},
    }


def test_provider_failure_does_not_contribute_twenty_five_safety_points(monkeypatch):
    monkeypatch.setattr(
        "app.domains.risk.service.get_recent_lawsuits",
        lambda *_args, **_kwargs: pytest.fail("must use frozen value"),
    )
    monkeypatch.setattr(
        "app.domains.risk.soft_risk.score_soft_risks",
        lambda *_args, **_kwargs: pytest.fail("must not call model"),
    )
    result = score_frozen_inputs(
        inputs(),
        [evidence("judicial"), evidence("soft", "unavailable")],
        current_policy(),
    )
    assert result["quality"] == "partial"
    assert result["risk_detail"]["data_coverage"]["available_dimensions"] == [
        "judicial"
    ]
    assert result["risk_score"] < 95
    assert result["score_breakdown"]["软指标"]["数据状态"] == "unavailable"
    assert result["score_breakdown"]["软指标"]["归一化"] is None


def test_no_usable_hard_evidence_returns_unavailable_without_a_score():
    result = score_frozen_inputs(
        inputs(), [evidence("judicial", "unavailable")], current_policy()
    )
    assert result["quality"] == "unavailable"
    assert result["risk_score"] is None


def test_single_dimension_coverage_does_not_require_unrequested_dimensions():
    result = score_frozen_inputs(
        inputs(), [evidence("judicial")], current_policy(["judicial"])
    )
    assert result["quality"] == "complete"
    assert result["risk_detail"]["data_coverage"]["coverage_ratio"] == 1
    assert result["risk_detail"]["data_coverage"]["missing_dimensions"] == []


def test_frozen_policy_threshold_changes_do_not_mutate_original_policy():
    policy = current_policy(["judicial"])
    changed = deepcopy(policy)
    changed["thresholds"]["low_risk"] = 99
    changed["thresholds"]["medium_risk"] = 95
    a = score_frozen_inputs(inputs(), [evidence("judicial")], policy)
    b = score_frozen_inputs(inputs(), [evidence("judicial")], changed)
    assert a["risk_score"] == b["risk_score"]
    assert a["risk_level"] != b["risk_level"]


def test_soft_failure_has_unavailable_status_and_no_zero_scores(monkeypatch):
    from app.domains.risk import soft_risk

    monkeypatch.setattr(soft_risk.cache_client, "get", lambda *_: None)
    monkeypatch.setattr(soft_risk, "_call_llm", lambda *_: None)
    result = soft_risk.score_soft_risks("甲公司", context={"known_evidence": "none"})
    assert result["status"] == "unavailable"
    assert all("score" not in item for item in result["dimensions"].values())


def test_unavailable_dimensions_do_not_publish_zero_findings():
    value = inputs()
    value["risk_detail"] = {
        "lawsuit_count": 0,
        "executed_count": 0,
        "abnormal_operation_count": 0,
        "administrative_penalty_count": 0,
    }
    result = score_frozen_inputs(
        value,
        [evidence("judicial"), evidence("operational", "unavailable")],
        current_policy(),
    )
    assert "abnormal_operation_count" not in result["risk_detail"]
    assert "administrative_penalty_count" not in result["risk_detail"]
    assert result["risk_detail"]["lawsuit_count"] == 0
