"""Executable promotion and rollback guards for the V2 rollout."""

from __future__ import annotations

from typing import Any, Protocol


STAGES = ("shadow", "internal", "canary", "default")
NEXT_STAGE = {stage: STAGES[index + 1] for index, stage in enumerate(STAGES[:-1])}
THRESHOLDS: dict[str, float] = {
    "eval_pass_rate": 1.0,
    "critical_missing_evidence_recommendations": 0.0,
    "identity_precision": 0.99,
    "requirement_accuracy": 0.95,
    "evidence_support_rate": 0.98,
    "macro_precision": 0.95,
    "macro_recall": 0.95,
    "citation_completeness": 1.0,
    "evidence_completeness": 1.0,
    "clarification_accuracy": 1.0,
    "unsafe_action_rate": 0.0,
    "duplicate_action_rate": 0.0,
    "recovery_success_rate": 0.99,
    "first_event_p95_ms": 2000.0,
    "local_candidate_p95_ms": 90000.0,
}
MIN_SAMPLE_COUNTS = {"shadow": 12, "internal": 50, "canary": 100}
class RolloutStateStore(Protocol):
    def get(self) -> dict[str, str]: ...
    def set(self, state: str, stage: str | None = None) -> dict[str, str]: ...


class InMemoryRolloutStateStore:
    """Test-only shared store; production uses PostgreSQL below."""

    def __init__(self, state: dict[str, str] | None = None) -> None:
        from app.core.config import settings

        self.state = state if state is not None else {
            "state": settings.AGENT_RUN_V2_ROLLOUT_STATE,
            "stage": settings.AGENT_RUN_V2_ROLLOUT,
        }
        self._explicit = state is not None

    def get(self) -> dict[str, str]:
        if not self._explicit:
            from app.core.config import settings

            return {"state": settings.AGENT_RUN_V2_ROLLOUT_STATE, "stage": settings.AGENT_RUN_V2_ROLLOUT}
        return dict(self.state)

    def set(self, state: str, stage: str | None = None) -> dict[str, str]:
        self._explicit = True
        self.state["state"] = state
        if stage is not None:
            self.state["stage"] = stage
        return self.get()


class PostgresRolloutStateStore:
    """Durable control-plane adapter. Database errors are intentionally propagated."""

    def get(self) -> dict[str, str]:
        from app.domains.agent_run.repo import get_rollout_control_state

        return get_rollout_control_state()

    def set(self, state: str, stage: str | None = None) -> dict[str, str]:
        from app.domains.agent_run.repo import set_rollout_control_state

        return set_rollout_control_state(state, stage)


_DEFAULT_STORE: RolloutStateStore = PostgresRolloutStateStore()


def get_rollout_state_store() -> RolloutStateStore:
    return _DEFAULT_STORE


def get_rollout_state_snapshot(*, store: RolloutStateStore | None = None) -> dict[str, str] | None:
    try:
        snapshot = (store or get_rollout_state_store()).get()
    except Exception:
        return None
    if snapshot.get("state") not in {"active", "rollback_frozen"} or snapshot.get("stage") not in STAGES:
        return None
    return snapshot


def is_rollout_frozen(config: Any, *, store: RolloutStateStore | None = None) -> bool:
    """Read durable state every time; unavailable control plane is frozen."""
    snapshot = get_rollout_state_snapshot(store=store)
    return snapshot is None or snapshot["state"] == "rollback_frozen"


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
            "first_event_p95_ms",
            "local_candidate_p95_ms",
        }
        if not isinstance(value, (int, float)) or (value > threshold if lower_is_better else value < threshold):
            reasons.append(f"threshold_failed:{key}")
    for key in ("in_flight_runs", "pending_proposals", "leased_outbox"):
        value = evidence.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            reasons.append(f"invalid_in_flight_count:{key}")
        elif value:
            reasons.append("in_flight_work_not_drained")
    if "in_flight_work_not_drained" in reasons:
        reasons.append("in_flight_work_not_drained")
    if not isinstance(approval, dict) or approval.get("decision") != "approved" or not approval.get("approver_id") or not approval.get("record_id"):
        reasons.append("human_approval_record_missing")
    return {
        "allowed": not reasons,
        "current_stage": current_stage,
        "target_stage": target,
        "reasons": reasons,
        "approval_record_id": approval.get("record_id") if isinstance(approval, dict) else None,
        "rollout_state": "active" if not reasons else "blocked",
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
        "in_flight_runs": {"pause_new_steps": True, "preserve_checkpoint": True, "count": work.get("runs", work.get("in_flight_runs", 0))},
        "rollout_state": "rollback_frozen",
        "resume_requires_approval": True,
        "reason": reason.strip(),
    }


def promote_rollout(config: Any, current_stage: str, evidence: dict[str, Any], *, approval: dict[str, Any] | None = None, store: RolloutStateStore | None = None) -> dict[str, Any]:
    """Apply an approved promotion to the runtime config; fail closed otherwise."""
    result = check_promotion(current_stage, evidence, approval=approval)
    if result["allowed"]:
        try:
            (store or get_rollout_state_store()).set("active", result["target_stage"])
        except Exception:
            return {**result, "allowed": False, "rollout_state": "unavailable", "reasons": [*result["reasons"], "rollout_control_plane_unavailable"]}
        config.AGENT_RUN_V2_ROLLOUT = result["target_stage"]
        config.AGENT_RUN_V2_ROLLOUT_STATE = "active"
    return result


def rollback_rollout(config: Any, stage: str, *, reason: str, in_flight: dict[str, int] | None = None, store: RolloutStateStore | None = None) -> dict[str, Any]:
    """Freeze new V2 and Shadow work before operators drain durable in-flight work."""
    result = check_rollback(stage, reason=reason, in_flight=in_flight)
    try:
        (store or get_rollout_state_store()).set("rollback_frozen", stage)
    except Exception:
        return {**result, "allowed": False, "rollout_state": "unavailable", "reasons": ["rollout_control_plane_unavailable"]}
    config.AGENT_RUN_V2_ROLLOUT_STATE = "rollback_frozen"
    return result
