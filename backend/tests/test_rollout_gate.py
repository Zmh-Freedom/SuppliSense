from app.core.rollout_gate import (
    check_promotion,
    check_rollback,
    is_rollout_frozen,
    promote_rollout,
    rollback_rollout,
)
from app.core.config import Settings
from app.core.rollout_gate import InMemoryRolloutStateStore


def _evidence() -> dict:
    return {
        "window_complete": True,
        "sample_count": 100,
        "eval_pass_rate": 1.0,
        "macro_precision": 0.99,
        "macro_recall": 0.99,
        "citation_completeness": 1.0,
        "evidence_completeness": 1.0,
        "clarification_accuracy": 1.0,
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


def test_latency_thresholds_are_lower_is_better_and_rollback_handles_runs():
    slow = _evidence()
    slow["first_event_p95_ms"] = 2001
    slow["local_candidate_p95_ms"] = 90001
    result = check_promotion("shadow", slow, approval={"decision": "approved", "approver_id": "a", "record_id": "r"})
    assert result["allowed"] is False
    assert "threshold_failed:first_event_p95_ms" in result["reasons"]
    assert "threshold_failed:local_candidate_p95_ms" in result["reasons"]

    fast = _evidence()
    fast["first_event_p95_ms"] = 100
    fast["local_candidate_p95_ms"] = 1000
    result = check_promotion("shadow", fast, approval={"decision": "approved", "approver_id": "a", "record_id": "r"})
    assert result["allowed"] is True

    rollback = check_rollback("canary", reason="unsafe action", in_flight={"runs": 2, "pending_proposals": 1, "leased_outbox": 2})
    assert rollback["in_flight_runs"]["pause_new_steps"] is True
    assert rollback["rollout_state"] == "rollback_frozen"


def test_rollback_rollout_freezes_runtime_and_promotion_unfreezes_it():
    config = Settings(_env_file=None, AGENT_RUN_V2_ENABLED=True, AGENT_RUN_V2_ROLLOUT="canary")
    store = InMemoryRolloutStateStore()
    frozen = rollback_rollout(config, "canary", reason="unsafe action", store=store)

    assert frozen["rollout_state"] == "rollback_frozen"
    assert is_rollout_frozen(config, store=store) is True

    promoted = promote_rollout(
        config,
        "canary",
        _evidence(),
        approval={"decision": "approved", "approver_id": "admin", "record_id": "approval"},
        store=store,
    )

    assert promoted["allowed"] is True
    assert config.AGENT_RUN_V2_ROLLOUT_STATE == "active"
    assert is_rollout_frozen(config, store=store) is False


def test_rollout_state_is_visible_to_independent_readers_and_pg_failure_fails_closed():
    first_process_store = InMemoryRolloutStateStore()
    second_process_store = InMemoryRolloutStateStore(first_process_store.state)
    config = Settings(_env_file=None, AGENT_RUN_V2_ENABLED=True, AGENT_RUN_V2_ROLLOUT="canary")

    rollback_rollout(config, "canary", reason="unsafe action", store=first_process_store)
    assert is_rollout_frozen(config, store=second_process_store) is True

    promote_rollout(config, "canary", _evidence(), approval={"decision": "approved", "approver_id": "admin", "record_id": "approval"}, store=first_process_store)
    assert is_rollout_frozen(config, store=second_process_store) is False

    class BrokenStore:
        def get(self):
            raise RuntimeError("control plane unavailable")

        def set(self, *_args, **_kwargs):
            raise RuntimeError("control plane unavailable")

    assert is_rollout_frozen(config, store=BrokenStore()) is True
    blocked = rollback_rollout(config, "canary", reason="unsafe action", store=BrokenStore())
    assert blocked["allowed"] is False
    assert "rollout_control_plane_unavailable" in blocked["reasons"]
