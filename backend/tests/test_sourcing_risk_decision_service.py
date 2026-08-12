"""Golden behavior tests for deterministic sourcing-risk candidate decisions."""

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
    return {
        "a": [_sanctions("clear", evidence_id="evidence-a")],
        "b": [_sanctions("clear", evidence_id="evidence-b")],
    }


def test_sanctions_hit_is_rejected_even_with_high_other_scores():
    """Skipping the sanctions gate would recommend a sanctioned supplier."""
    decisions = decide_candidates(
        _requirement(), _policy(), [_candidate("a")], {"a": [_sanctions("hit")]}
    )

    assert decisions[0]["group"] == "rejected"
    assert decisions[0]["final_score"] is None
    assert "SANCTIONS_HIT" in decisions[0]["reason_codes"]


def test_missing_financial_data_penalizes_but_does_not_claim_low_risk():
    """Treating absent financial data as a good risk score would overstate confidence."""
    candidate = _candidate("a", dimension_scores={
        "match": 100,
        "capacity": 80,
        "performance": 80,
        "quality": 80,
        "risk": None,
        "commercial": 70,
    })

    decision = decide_candidates(
        _requirement(), _policy(), [candidate], {"a": [_sanctions("clear")]}
    )[0]

    assert decision["group"] == "alternative"
    assert decision["final_score"] == 62.0
    assert decision["missing_data_penalty"] == 10.0
    assert "MISSING_FINANCIAL_DATA" in decision["reason_codes"]
    assert decision["confidence"] < 1


def test_equal_scores_sort_by_company_id():
    """Depending on provider order for ties would make the shortlist non-repeatable."""
    decisions = decide_candidates(
        _requirement(), _policy(), [_candidate("b"), _candidate("a")], _clear_evidence()
    )

    assert [item["company_id"] for item in decisions] == ["a", "b"]


def test_eligible_candidate_returns_weighted_score_and_audit_snapshot():
    """Omitting dimension evidence or policy provenance would prevent decision audit."""
    decision = decide_candidates(
        _requirement(), _policy(), [_candidate("a")], {"a": [_sanctions("clear", evidence_id="ev-1")]}
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
        [_candidate("a", stale_dimensions=["risk"])],
        {"a": [_sanctions("clear")]},
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
            _candidate("identity", identity_status="pending_verification", score_eligible=False),
            _candidate("sanctions"),
        ],
        {
            "identity": [_sanctions("clear")],
            "sanctions": [_sanctions("unavailable")],
        },
    )

    assert [(item["company_id"], item["group"], item["final_score"]) for item in decisions] == [
        ("identity", "needs_review", None),
        ("sanctions", "needs_review", None),
    ]
    assert "IDENTITY_UNVERIFIED" in decisions[0]["reason_codes"]
    assert "SANCTIONS_DATA_UNAVAILABLE" in decisions[1]["reason_codes"]
