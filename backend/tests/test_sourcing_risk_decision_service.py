"""Golden behavior tests for deterministic sourcing-risk candidate decisions."""

from copy import deepcopy
from uuid import uuid4

import pytest

from app.domains.sourcing_risk.decision_service import decide_candidates


def _requirement() -> dict:
    return {
        "category": "摄像头",
        "specification": "4K",
        "qualifications": "ISO 9001",
    }


def _policy() -> dict:
    return {
        "weights": {
            "match": 0.25,
            "capacity": 0.20,
            "performance": 0.15,
            "quality": 0.15,
            "risk": 0.15,
            "commercial": 0.10,
        },
        "missing_penalties": {
            "match": 0.10,
            "capacity": 0.08,
            "performance": 0.06,
            "quality": 0.06,
            "risk": 0.10,
            "commercial": 0.04,
        },
        "stale_penalties": {
            "match": 0.04,
            "capacity": 0.05,
            "performance": 0.04,
            "quality": 0.04,
            "risk": 0.08,
            "commercial": 0.03,
        },
        "required_evidence": ["sanctions"],
        "hard_gates": {
            "unmatched_category": "rejected",
            "supplier_not_active": "rejected",
            "mandatory_qualification_missing": "rejected",
            "identity_unverified": "needs_review",
            "sanctions_hit": "rejected",
            "sanctions_available": "needs_review",
            "key_evidence_conflict": "needs_review",
        },
        "template_id": "sourcing-risk-default",
        "template_version": "1.0.0",
        "scoring_version": "1.0.0",
        "checksum": "policy-checksum",
    }


def _candidate(company_id: str, **overrides: object) -> dict:
    candidate = {
        "company_id": company_id,
        "identity_company_id": company_id,
        "identity_status": "exact",
        "score_eligible": True,
        "active": True,
        "categories": ["摄像头"],
        "qualifications": ["ISO 9001"],
        "dimension_scores": {
            "match": 100,
            "capacity": 80,
            "performance": 80,
            "quality": 80,
            "risk": 90,
            "commercial": 70,
        },
    }
    candidate.update(overrides)
    return candidate


def _sanctions(claim_code: str, **overrides: object) -> dict:
    evidence = {
        "evidence_id": f"sanctions-{claim_code}",
        "dimension": "sanctions",
        "claim_code": claim_code,
        "freshness_status": "fresh",
        "conflict_status": "none",
        "confidence": 1.0,
    }
    evidence.update(overrides)
    return evidence


def _clear_evidence() -> dict[str, list[dict]]:
    company_a = "00000000-0000-0000-0000-000000000001"
    company_b = "00000000-0000-0000-0000-000000000002"
    return {
        company_a: [_sanctions("clear", evidence_id="evidence-a")],
        company_b: [_sanctions("clear", evidence_id="evidence-b")],
    }


def test_sanctions_hit_is_rejected_even_with_high_other_scores():
    """Skipping the sanctions gate would recommend a sanctioned supplier."""
    decisions = decide_candidates(
        _requirement(), _policy(), [_candidate(str(uuid4()))], {}
    )

    company_id = decisions[0]["company_id"]
    decisions = decide_candidates(
        _requirement(), _policy(), [_candidate(company_id)], {company_id: [_sanctions("hit")]}
    )

    assert decisions[0]["group"] == "rejected"
    assert decisions[0]["final_score"] is None
    assert "SANCTIONS_HIT" in decisions[0]["reason_codes"]


def test_missing_financial_data_penalizes_but_does_not_claim_low_risk():
    """Treating absent financial data as a good risk score would overstate confidence."""
    company_id = str(uuid4())
    candidate = _candidate(company_id, dimension_scores={
        "match": 100,
        "capacity": 80,
        "performance": 80,
        "quality": 80,
        "risk": None,
        "commercial": 70,
    })

    decision = decide_candidates(
        _requirement(), _policy(), [candidate], {company_id: [_sanctions("clear")]}
    )[0]

    assert decision["group"] == "alternative"
    assert decision["final_score"] == 62.0
    assert decision["missing_data_penalty"] == 10.0
    assert "MISSING_FINANCIAL_DATA" in decision["reason_codes"]
    assert decision["confidence"] < 1


def test_equal_scores_sort_by_company_id():
    """Depending on provider order for ties would make the shortlist non-repeatable."""
    decisions = decide_candidates(
        _requirement(),
        _policy(),
        [
            _candidate("00000000-0000-0000-0000-000000000002"),
            _candidate("00000000-0000-0000-0000-000000000001"),
        ],
        _clear_evidence(),
    )

    assert [item["company_id"] for item in decisions] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]


def test_eligible_candidate_returns_weighted_score_and_audit_snapshot():
    """Omitting dimension evidence or policy provenance would prevent decision audit."""
    decision = decide_candidates(
        _requirement(),
        _policy(),
        [_candidate("00000000-0000-0000-0000-000000000001")],
        {"00000000-0000-0000-0000-000000000001": [_sanctions("clear", evidence_id="ev-1")]},
    )[0]

    assert decision["group"] == "recommended"
    assert decision["final_score"] == 85.5
    assert decision["dimension_scores"] == {
        "match": 100.0,
        "capacity": 80.0,
        "performance": 80.0,
        "quality": 80.0,
        "risk": 90.0,
        "commercial": 70.0,
    }
    assert decision["evidence_ids"] == ["ev-1"]
    assert decision["policy_snapshot"] == _policy()


def test_stale_dimension_receives_policy_penalty_in_final_score():
    """Ignoring a stale dimension would promote a score based on expired evidence."""
    decision = decide_candidates(
        _requirement(),
        _policy(),
        [_candidate("00000000-0000-0000-0000-000000000001", stale_dimensions=["risk"])],
        {"00000000-0000-0000-0000-000000000001": [_sanctions("clear")]},
    )[0]

    assert decision["final_score"] == 77.5
    assert decision["stale_data_penalty"] == 8.0
    assert "STALE_RISK_DATA" in decision["reason_codes"]


def test_unverified_identity_and_unavailable_sanctions_are_needs_review_without_score():
    """Scoring candidates with failed identity or unavailable sanctions bypasses hard gates."""
    decisions = decide_candidates(
        _requirement(),
        _policy(),
        [
            _candidate("00000000-0000-0000-0000-000000000001", identity_status="pending_verification", score_eligible=False),
            _candidate("00000000-0000-0000-0000-000000000002"),
        ],
        {
            "00000000-0000-0000-0000-000000000001": [_sanctions("clear")],
            "00000000-0000-0000-0000-000000000002": [_sanctions("unavailable")],
        },
    )

    assert [(item["company_id"], item["group"], item["final_score"]) for item in decisions] == [
        ("00000000-0000-0000-0000-000000000001", "needs_review", None),
        ("00000000-0000-0000-0000-000000000002", "needs_review", None),
    ]
    assert "IDENTITY_UNVERIFIED" in decisions[0]["reason_codes"]
    assert "SANCTIONS_DATA_UNAVAILABLE" in decisions[1]["reason_codes"]


@pytest.mark.parametrize(
    "weights",
    [
        {"match": 1.0},
        {**_policy()["weights"], "illegal": 0.0},
    ],
)
def test_invalid_weight_dimension_set_is_rejected_before_candidate_decision(weights):
    """A partial or extended weight policy makes a decision formula ambiguous."""
    policy = _policy()
    policy["weights"] = weights

    with pytest.raises(ValueError, match="weights must define exactly"):
        decide_candidates(_requirement(), policy, [], {})


def test_sanctions_hit_precedes_earlier_identity_review_gate_in_policy_order():
    """A policy ordering change must not downgrade a sanctions hit to review."""
    company_id = str(uuid4())
    policy = _policy()
    policy["hard_gates"] = {
        "identity_unverified": "needs_review",
        "sanctions_hit": "rejected",
        **{key: value for key, value in policy["hard_gates"].items() if key not in {"identity_unverified", "sanctions_hit"}},
    }

    decision = decide_candidates(
        _requirement(),
        policy,
        [_candidate(company_id, identity_status="pending_verification", score_eligible=False)],
        {company_id: [_sanctions("hit")]},
    )[0]

    assert (decision["group"], decision["reason_codes"], decision["final_score"]) == (
        "rejected",
        ["SANCTIONS_HIT"],
        None,
    )


def test_unavailable_sanctions_precedes_earlier_identity_review_gate_in_policy_order():
    """A failed sanctions lookup must retain its fail-closed reason over identity review."""
    company_id = str(uuid4())
    policy = _policy()
    policy["hard_gates"] = {
        "identity_unverified": "needs_review",
        "sanctions_available": "needs_review",
        **{key: value for key, value in policy["hard_gates"].items() if key not in {"identity_unverified", "sanctions_available"}},
    }

    decision = decide_candidates(
        _requirement(),
        policy,
        [_candidate(company_id, identity_status="pending_verification", score_eligible=False)],
        {company_id: [_sanctions("unavailable")]},
    )[0]

    assert (decision["group"], decision["reason_codes"], decision["final_score"]) == (
        "needs_review",
        ["SANCTIONS_DATA_UNAVAILABLE"],
        None,
    )


@pytest.mark.parametrize(
    "company_id, identity_company_id",
    [
        ("", ""),
        ("not-a-uuid", "not-a-uuid"),
        ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"),
    ],
)
def test_exact_score_eligible_identity_requires_matching_valid_canonical_company_id(
    company_id, identity_company_id
):
    """Scoring a malformed or mismatched canonical identity corrupts company attribution."""
    decision = decide_candidates(
        _requirement(),
        _policy(),
        [_candidate(company_id, identity_company_id=identity_company_id)],
        {company_id: [_sanctions("clear")]},
    )[0]

    assert decision["group"] == "needs_review"
    assert decision["final_score"] is None
    assert decision["reason_codes"] == ["IDENTITY_UNVERIFIED"]
