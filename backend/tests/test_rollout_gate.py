from app.core.rollout_gate import check_promotion, check_rollback


def _evidence() -> dict:
    return {
        "window_complete": True,
        "sample_count": 100,
        "eval_pass_rate": 1.0,
        "critical_missing_evidence_recommendations": 0.0,
        "identity_precision": 0.99,
        "requirement_accuracy": 0.95,
        "evidence_support_rate": 0.98,
        "unsafe_action_rate": 0.0,
        "duplicate_action_rate": 0.0,
        "recovery_success_rate": 0.99,
        "first_event_p95_ms": 2000,
        "local_candidate_p95_ms": 90000,
        "in_flight_runs": 0,
        "pending_proposals": 0,
        "leased_outbox": 0,
    }


def test_promotion_requires_evidence_and_human_approval_record():
    blocked = check_promotion("shadow", _evidence())
    assert blocked["allowed"] is False
    assert "human_approval_record_missing" in blocked["reasons"]

    allowed = check_promotion(
        "shadow", _evidence(), approval={"decision": "approved", "approver_id": "admin-1", "record_id": "approval-1"}
    )
    assert allowed["allowed"] is True
    assert allowed["target_stage"] == "internal"


def test_promotion_blocks_in_flight_work_and_rollback_freezes_actions():
    evidence = _evidence()
    evidence["pending_proposals"] = 1
    result = check_promotion("canary", evidence, approval={"decision": "approved", "approver_id": "a", "record_id": "r"})
    assert result["allowed"] is False
    assert "in_flight_work_not_drained" in result["reasons"]

    rollback = check_rollback("canary", reason="unsafe action", in_flight={"pending_proposals": 1, "leased_outbox": 2})
    assert rollback["freeze_new_v2_actions"] is True
    assert rollback["preserve_runs_checkpoints_audit"] is True
    assert rollback["leased_outbox"]["finish_or_expire"] == 2
