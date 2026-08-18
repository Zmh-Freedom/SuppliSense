"""Pure decision helpers for bounded Agent execution loops."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.graphs.agent_core.contracts import LoopState


LoopDecisionStatus = Literal[
    "continue",
    "completed",
    "partial",
    "needs_review",
    "blocked",
]


class LoopDecision(BaseModel):
    """One explicit, auditable decision made after a loop iteration."""

    status: LoopDecisionStatus
    stop_reason: str = Field(min_length=1)


def build_tool_fingerprint(tool_name: str, tool_arguments: dict[str, Any]) -> str:
    """Return a stable fingerprint for one tool invocation and its arguments."""
    serialized_arguments = json.dumps(
        tool_arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    raw_value = f"{tool_name.strip()}:{serialized_arguments}"
    return hashlib.sha256(raw_value.encode("utf-8")).hexdigest()


def evaluate_loop(
    loop_state: LoopState,
    *,
    current_fingerprint: str | None,
    evidence_count: int,
    now: datetime | None = None,
    is_complete: bool = False,
    requires_review: bool = False,
) -> LoopDecision:
    """Decide whether a bounded loop may continue without mutating its state."""
    if evidence_count < 0:
        raise ValueError("evidence_count must not be negative")

    if is_complete:
        return LoopDecision(status="completed", stop_reason="completion_criteria_met")
    if requires_review:
        return LoopDecision(status="needs_review", stop_reason="manual_review_required")

    current_time = now or datetime.now(timezone.utc)
    if _has_timed_out(loop_state, current_time):
        return _budget_exhausted_decision(loop_state, "timeout")
    if loop_state.iteration >= loop_state.max_iterations:
        return _budget_exhausted_decision(loop_state, "iteration_budget_exhausted")
    if loop_state.tool_call_count >= loop_state.max_tool_calls:
        return _budget_exhausted_decision(loop_state, "tool_budget_exhausted")

    has_new_evidence = evidence_count > loop_state.evidence_count_before
    if current_fingerprint and current_fingerprint == loop_state.previous_fingerprint:
        if not has_new_evidence:
            return LoopDecision(
                status="blocked",
                stop_reason="repeated_fingerprint_without_new_evidence",
            )
    if not has_new_evidence:
        return LoopDecision(status="needs_review", stop_reason="no_new_evidence")
    return LoopDecision(status="continue", stop_reason="within_budget")


def _has_timed_out(loop_state: LoopState, current_time: datetime) -> bool:
    started_at = loop_state.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    elapsed_seconds = (current_time - started_at).total_seconds()
    return elapsed_seconds >= loop_state.timeout_seconds


def _budget_exhausted_decision(loop_state: LoopState, reason: str) -> LoopDecision:
    if loop_state.evidence_count_before > 0:
        return LoopDecision(status="partial", stop_reason=reason)
    return LoopDecision(status="blocked", stop_reason=reason)
