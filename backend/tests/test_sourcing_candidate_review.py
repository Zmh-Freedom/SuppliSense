from copy import deepcopy

import pytest

from app.domains.sourcing.candidate_review import apply_candidate_decisions


def test_pending_candidate_can_be_deferred_without_blocking_exact_candidate():
    candidates = [
        {"candidate_key": "candidate-a", "company_id": "company-a", "identity_status": "exact"},
        {
            "candidate_key": "candidate-b",
            "company_id": None,
            "identity_status": "candidates",
            "identity_candidates": [{"company_id": "company-b"}],
        },
    ]

    result = apply_candidate_decisions(
        candidates,
        [{"candidate_key": "candidate-b", "action": "defer", "reason": "候选主体需核实"}],
    )

    assert [candidate["company_id"] for candidate in result["eligible"]] == ["company-a"]
    assert [candidate["candidate_key"] for candidate in result["deferred"]] == ["candidate-b"]
    assert result["excluded"] == []


def test_all_excluded_candidates_produce_no_eligible_candidates():
    candidates = [
        {"candidate_key": "candidate-a", "company_id": None, "identity_status": "pending_verification", "identity_candidates": []},
        {"candidate_key": "candidate-b", "company_id": None, "identity_status": "candidates", "identity_candidates": []},
    ]

    result = apply_candidate_decisions(
        candidates,
        [
            {"candidate_key": "candidate-a", "action": "exclude", "reason": "不符合采购范围"},
            {"candidate_key": "candidate-b", "action": "exclude", "reason": "重复供应商"},
        ],
    )

    assert result["eligible"] == []
    assert {candidate["candidate_key"] for candidate in result["excluded"]} == {"candidate-a", "candidate-b"}


def test_confirm_rejects_company_id_outside_current_identity_options():
    candidates = [
        {
            "candidate_key": "candidate-a",
            "company_id": None,
            "identity_status": "candidates",
            "identity_candidates": [{"company_id": "company-a"}],
        }
    ]

    with pytest.raises(ValueError, match="当前候选主体选项"):
        apply_candidate_decisions(
            candidates,
            [{"candidate_key": "candidate-a", "action": "confirm", "company_id": "forged-company"}],
        )


def test_duplicate_candidate_keys_are_rejected():
    candidates = [
        {"candidate_key": "candidate-a", "company_id": None, "identity_status": "pending_verification"},
        {"candidate_key": "candidate-a", "company_id": None, "identity_status": "pending_verification"},
    ]

    with pytest.raises(ValueError, match="candidate_key.*重复"):
        apply_candidate_decisions(candidates, [])


def test_exact_candidate_stays_eligible_when_not_submitted():
    candidate = {"candidate_key": "candidate-a", "company_id": "company-a", "identity_status": "exact", "score_eligible": True}

    result = apply_candidate_decisions([candidate], [])

    assert result["eligible"] == [candidate]
    assert result["deferred"] == []


def test_omitted_pending_candidate_defaults_to_defer_with_fixed_reason():
    candidate = {"candidate_key": "candidate-a", "company_id": None, "identity_status": "pending_verification"}

    result = apply_candidate_decisions([candidate], [])

    assert result["eligible"] == []
    assert result["deferred"][0]["disposition"] == "defer"
    assert result["deferred"][0]["reason"] == "not_selected_this_round"


def test_candidate_review_does_not_mutate_input():
    candidates = [
        {"candidate_key": "candidate-a", "company_id": None, "identity_status": "candidates", "identity_candidates": [{"company_id": "company-a"}]}
    ]
    decisions = [{"candidate_key": "candidate-a", "action": "confirm", "company_id": "company-a"}]
    original_candidates = deepcopy(candidates)
    original_decisions = deepcopy(decisions)

    apply_candidate_decisions(candidates, decisions)

    assert candidates == original_candidates
    assert decisions == original_decisions


def test_duplicate_decisions_are_rejected():
    candidates = [{"candidate_key": "candidate-a", "company_id": "company-a", "identity_status": "exact"}]
    decisions = [
        {"candidate_key": "candidate-a", "action": "exclude", "reason": "原因一"},
        {"candidate_key": "candidate-a", "action": "defer", "reason": "原因二"},
    ]

    with pytest.raises(ValueError, match="candidate_key.*重复"):
        apply_candidate_decisions(candidates, decisions)
