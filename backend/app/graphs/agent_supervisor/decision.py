"""Pure decision construction for the Agent Supervisor graph."""

from uuid import NAMESPACE_URL, uuid5

from app.graphs.agent_supervisor.contracts import (
    AgentFinding,
    DecisionResult,
    EvidenceMergeResult,
    PendingApproval,
    RecommendedAction,
)
from app.graphs.agent_supervisor.state import AgentTaskState


_RISK_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _findings(state: AgentTaskState) -> list[AgentFinding]:
    return [AgentFinding.model_validate(item) for item in state.get("findings", [])]


def _recommendations(state: AgentTaskState) -> list[RecommendedAction]:
    return [RecommendedAction.model_validate(item) for item in state.get("recommendations", [])]


def _risk_level(findings: list[AgentFinding]) -> str:
    return max((finding.level for finding in findings), key=_RISK_RANK.__getitem__, default="unknown")


def _approval_for(action: RecommendedAction) -> PendingApproval:
    approval_key = f"{action.action_type}:{sorted(action.target.items())}:{action.reason}"
    return PendingApproval(
        approval_id=str(uuid5(NAMESPACE_URL, approval_key)),
        action_type=action.action_type,
        target=action.target,
        reason=action.reason,
        impact=action.impact or "待人工确认的操作影响",
        status="pending",
    )


def build_decision(state: AgentTaskState, merged: EvidenceMergeResult) -> DecisionResult:
    """Create recommendations and approval requests without executing any action."""
    findings = _findings(state)
    risk_level = _risk_level(findings)
    if merged.requires_review:
        missing = "、".join(merged.missing_dimensions)
        reasons = [reason for reason in ("存在冲突证据" if merged.conflicts else "", f"缺少{missing}证据" if missing else "") if reason]
        return DecisionResult(
            summary="证据需人工复核：" + "；".join(reasons),
            risk_level=risk_level,
            requires_review=True,
        )

    recommendations = _recommendations(state)
    approvals = [_approval_for(action) for action in recommendations]
    return DecisionResult(
        summary=f"基于 {len(merged.evidence)} 条证据形成风险结论，综合可信度 {merged.overall_confidence:.2f}。",
        risk_level=risk_level,
        recommendations=recommendations,
        pending_approvals=approvals,
    )
