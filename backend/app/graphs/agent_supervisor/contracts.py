"""Pydantic contracts shared by the Agent Supervisor graph."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


AgentName = Literal["sourcing", "risk", "compliance", "sentiment"]
AgentResultStatus = Literal["completed", "failed", "skipped", "needs_review"]
EvidenceSourceType = Literal["official", "registry", "third_party", "news", "internal", "unknown"]
EvidenceFreshness = Literal["fresh", "stale", "unknown"]
FindingLevel = Literal["low", "medium", "high", "critical", "unknown"]
ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]


class PlannerTask(BaseModel):
    task_id: str = Field(min_length=1)
    agent: AgentName
    depends_on: list[str] = Field(default_factory=list)
    required: bool = True


class TaskPlan(BaseModel):
    tasks: list[PlannerTask] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    evidence_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_type: EvidenceSourceType
    collected_at: str | None = None
    freshness: EvidenceFreshness = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    company_id: str | None = None
    dimension: str | None = None
    claim: str | None = None


class AgentFinding(BaseModel):
    type: str = Field(min_length=1)
    level: FindingLevel
    title: str = Field(min_length=1)
    description: str | None = None
    impact: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)


class RecommendedAction(BaseModel):
    action_type: str = Field(min_length=1)
    target: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1)
    impact: str | None = None
    requires_approval: bool = True

    @model_validator(mode="after")
    def require_approval(self) -> "RecommendedAction":
        self.requires_approval = True
        return self


class AgentMetrics(BaseModel):
    duration_ms: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0)


class AgentError(BaseModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False


class AgentResult(BaseModel):
    agent: AgentName
    status: AgentResultStatus
    summary: str
    findings: list[AgentFinding] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    metrics: AgentMetrics = Field(default_factory=AgentMetrics)
    error: AgentError | None = None

    @model_validator(mode="after")
    def validate_finding_evidence(self) -> "AgentResult":
        if self.status == "completed":
            evidence_ids = {item.evidence_id for item in self.evidence}
            missing = [
                finding.title
                for finding in self.findings
                if not finding.evidence_ids
                or not set(finding.evidence_ids).issubset(evidence_ids)
            ]
            if missing:
                raise ValueError(
                    "completed findings require references to returned evidence: "
                    + ", ".join(missing)
                )
        return self


class PendingApproval(BaseModel):
    approval_id: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    target: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    status: ApprovalStatus
    expires_at: str | None = None
    requires_approval: bool = True

    @model_validator(mode="after")
    def require_approval(self) -> "PendingApproval":
        self.requires_approval = True
        return self


class DecisionResult(BaseModel):
    summary: str
    risk_level: FindingLevel = "unknown"
    recommendations: list[RecommendedAction] = Field(default_factory=list)
    pending_approvals: list[PendingApproval] = Field(default_factory=list)
    requires_review: bool = False
