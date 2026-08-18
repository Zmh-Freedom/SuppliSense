"""Pure evidence completeness and bounded validation decisions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field

from app.graphs.agent_core.contracts import LoopState
from app.graphs.agent_core.loop import evaluate_loop
from app.graphs.agent_supervisor.contracts import (
    AgentResult,
    EvidenceMergeResult,
    TaskPlan,
)


ValidationStatus = Literal["completed", "partial", "needs_review", "blocked"]


class EvidenceValidationResult(BaseModel):
    """Auditable result of checking evidence against required task dimensions."""

    coverage: float = Field(ge=0.0, le=1.0)
    required_dimensions: list[str] = Field(default_factory=list)
    covered_dimensions: list[str] = Field(default_factory=list)
    conflict_dimensions: list[str] = Field(default_factory=list)
    insufficient_dimensions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    status: ValidationStatus
    stop_reason: str
    should_remediate: bool = False
    can_recommend: bool = False


def validate_evidence(
    merged: EvidenceMergeResult,
    plan: TaskPlan,
    results: Mapping[str, AgentResult],
    loop_state: LoopState,
    *,
    remediation_attempts: int = 0,
) -> EvidenceValidationResult:
    """Check usable coverage and decide whether one bounded repair is allowed."""
    required_dimensions = list(dict.fromkeys(
        task.agent for task in plan.tasks if task.required
    ))
    evidence_dimensions = {
        item.dimension for item in merged.evidence if item.dimension
    }
    conflict_dimensions = sorted({
        item.dimension
        for conflict in merged.conflicts
        for item in conflict
        if item.dimension in required_dimensions
    })
    missing_dimensions = set(merged.missing_dimensions)
    incomplete_dimensions = {
        task.agent
        for task in plan.tasks
        if task.required
        and (
            task.task_id not in results
            or results[task.task_id].status != "completed"
            or task.agent not in evidence_dimensions
        )
    }
    insufficient_dimensions = [
        dimension
        for dimension in required_dimensions
        if dimension in missing_dimensions
        or dimension in incomplete_dimensions
        or dimension in conflict_dimensions
    ]
    covered_dimensions = [
        dimension for dimension in required_dimensions
        if dimension not in insufficient_dimensions
    ]
    coverage = (
        len(covered_dimensions) / len(required_dimensions)
        if required_dimensions else 1.0
    )
    limitations = [
        f"{dimension} 维度缺少可用的独立证据。"
        for dimension in insufficient_dimensions
        if dimension not in conflict_dimensions
    ]
    limitations.extend(
        f"{dimension} 维度存在相互冲突的证据，需人工复核。"
        for dimension in conflict_dimensions
    )

    if conflict_dimensions:
        decision = evaluate_loop(
            loop_state,
            current_fingerprint="evidence-validation-conflict",
            evidence_count=len(merged.evidence),
            requires_review=True,
        )
        return EvidenceValidationResult(
            coverage=coverage,
            required_dimensions=required_dimensions,
            covered_dimensions=covered_dimensions,
            conflict_dimensions=conflict_dimensions,
            insufficient_dimensions=insufficient_dimensions,
            limitations=limitations,
            status="needs_review",
            stop_reason=decision.stop_reason,
        )

    if not insufficient_dimensions:
        decision = evaluate_loop(
            loop_state,
            current_fingerprint="evidence-validation-complete",
            evidence_count=len(merged.evidence),
            is_complete=True,
        )
        return EvidenceValidationResult(
            coverage=coverage,
            required_dimensions=required_dimensions,
            covered_dimensions=covered_dimensions,
            status="completed",
            stop_reason=decision.stop_reason,
            can_recommend=True,
        )

    decision = evaluate_loop(
        loop_state,
        current_fingerprint="evidence-validation-remediation",
        evidence_count=len(merged.evidence),
    )
    if decision.status == "continue" and remediation_attempts == 0:
        return EvidenceValidationResult(
            coverage=coverage,
            required_dimensions=required_dimensions,
            covered_dimensions=covered_dimensions,
            insufficient_dimensions=insufficient_dimensions,
            limitations=limitations,
            status="partial",
            stop_reason="validation_remediation_requested",
            should_remediate=True,
        )
    if decision.status == "continue":
        return EvidenceValidationResult(
            coverage=coverage,
            required_dimensions=required_dimensions,
            covered_dimensions=covered_dimensions,
            insufficient_dimensions=insufficient_dimensions,
            limitations=limitations,
            status="partial",
            stop_reason="validation_remediation_exhausted",
        )
    return EvidenceValidationResult(
        coverage=coverage,
        required_dimensions=required_dimensions,
        covered_dimensions=covered_dimensions,
        insufficient_dimensions=insufficient_dimensions,
        limitations=limitations,
        status="needs_review" if decision.status == "needs_review" else "partial",
        stop_reason=decision.stop_reason,
    )
