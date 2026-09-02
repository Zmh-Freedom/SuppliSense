"""Evidence-gated final answer contract for Agent graph outputs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.graphs.agent_core.evidence_ledger import Claim, EvidenceLedger, ValidatedClaim


class AgentAnswer(BaseModel):
    """Only validated claims are allowed into the final answer facts."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "partial", "needs_review", "failed"]
    summary: str = Field(min_length=1, max_length=12000)
    claims: list[ValidatedClaim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    action_proposals: list[dict[str, Any]] = Field(default_factory=list)
    action_receipts: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


def build_agent_answer(
    *,
    summary: str,
    ledger: EvidenceLedger,
    claims: list[Claim],
    required_dimensions: list[str] | None = None,
    action_proposals: list[dict[str, Any]] | None = None,
    action_receipts: list[dict[str, Any]] | None = None,
) -> AgentAnswer:
    """Validate claims and produce a truthful completed/partial answer."""
    validated = [ledger.validate_claim(claim).claim for claim in claims]
    accepted = [claim for claim in validated if claim.validation_status != "unsupported"]
    limitations: list[str] = []
    for claim in validated:
        limitations.extend(claim.validation_reasons)
    coverage = ledger.coverage(required_dimensions or [])
    limitations.extend(f"缺少 {dimension} 维度的正式证据" for dimension in coverage.missing_dimensions)
    limitations = list(dict.fromkeys(limitations))
    has_review_condition = any(
        claim.validation_status in {"unsupported", "conflicting"} for claim in validated
    ) or bool(coverage.missing_dimensions)
    has_partial = any(claim.validation_status == "partial" for claim in accepted)
    status: Literal["completed", "partial", "needs_review", "failed"]
    if has_review_condition:
        status = "needs_review"
    elif has_partial:
        status = "partial"
    else:
        status = "completed"
    evidence_refs = list(dict.fromkeys(ref for claim in accepted for ref in claim.evidence_refs))
    return AgentAnswer(
        status=status,
        summary=summary,
        claims=accepted,
        limitations=limitations,
        action_proposals=action_proposals or [],
        action_receipts=action_receipts or [],
        evidence_refs=evidence_refs,
    )


__all__ = ["AgentAnswer", "build_agent_answer"]
