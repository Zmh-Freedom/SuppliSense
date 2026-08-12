"""Executable promotion and rollback guards for the V2 rollout."""

from __future__ import annotations

from typing import Any


STAGES = ("shadow", "internal", "canary", "default")
NEXT_STAGE = {stage: STAGES[index + 1] for index, stage in enumerate(STAGES[:-1])}
THRESHOLDS: dict[str, float] = {
    "eval_pass_rate": 1.0,
    "critical_missing_evidence_recommendations": 0.0,
    "identity_precision": 0.99,
    "requirement_accuracy": 0.95,
    "evidence_support_rate": 0.98,
    "unsafe_action_rate": 0.0,
    "duplicate_action_rate": 0.0,
    "recovery_success_rate": 0.99,
    "first_event_p95_ms": 2000.0,
    "local_candidate_p95_ms": 90000.0,
}
MIN_SAMPLE_COUNTS = {"shadow": 12, "internal": 50, "canary": 100}


def check_promotion(
    current_stage: str,
    evidence: dict[str, Any],
    *,
    approval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic, auditable promotion decision; never mutate state."""
    if current_stage not in NEXT_STAGE:
        return {"allowed": False, "target_stage": None, "reasons": ["stage_has_no_promotion"]}
    target = NEXT_STAGE[current_stage]
    reasons: list[str] = []
    if evidence.get("window_complete") is not True:
        reasons.append("observation_window_incomplete")
    if not isinstance(evidence.get("sample_count"), int) or evidence["sample_count"] < MIN_SAMPLE_COUNTS[current_stage]:
        reasons.append(f"sample_count_below:{MIN_SAMPLE_COUNTS[current_stage]}")
    for key, threshold in THRESHOLDS.items():
        value = evidence.get(key)
        lower_is_better = key in {
            "critical_missing_evidence_recommendations",
            "unsafe_action_rate",
            "duplicate_action_rate",
        }
        if not isinstance(value, (int, float)) or (value > threshold if lower_is_better else value < threshold):
            reasons.append(f"threshold_failed:{key}")
    if evidence.get("in_flight_runs", 0) or evidence.get("pending_proposals", 0) or evidence.get("leased_outbox", 0):
        reasons.append("in_flight_work_not_drained")
    if not isinstance(approval, dict) or approval.get("decision") != "approved" or not approval.get("approver_id") or not approval.get("record_id"):
        reasons.append("human_approval_record_missing")
    return {
        "allowed": not reasons,
        "current_stage": current_stage,
        "target_stage": target,
        "reasons": reasons,
        "approval_record_id": approval.get("record_id") if isinstance(approval, dict) else None,
    }


def check_rollback(stage: str, *, reason: str, in_flight: dict[str, int] | None = None) -> dict[str, Any]:
    """Return the required rollback disposition, preserving durable audit state."""
    if stage not in STAGES:
        raise ValueError("unknown rollout stage")
    if len(reason.strip()) < 2:
        raise ValueError("rollback reason is required")
    work = in_flight or {}
    return {
        "allowed": True,
        "stage": stage,
        "freeze_new_v2_actions": True,
        "preserve_runs_checkpoints_audit": True,
        "pending_proposals": {"freeze": work.get("pending_proposals", 0), "manual_review": True},
        "leased_outbox": {"stop_new_leases": True, "finish_or_expire": work.get("leased_outbox", 0)},
        "resume_requires_approval": True,
        "reason": reason.strip(),
    }
