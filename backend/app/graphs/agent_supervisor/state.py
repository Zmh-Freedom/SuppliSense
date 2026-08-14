"""Checkpointable state boundary for the Agent Supervisor graph."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


TaskStatus = Literal[
    "CREATED",
    "PLANNING",
    "EXECUTING",
    "EVIDENCE_MERGING",
    "DECISION_READY",
    "WAITING_HUMAN_APPROVAL",
    "COMPLETED",
    "PARTIAL_COMPLETED",
    "NEEDS_CLARIFICATION",
    "FAILED",
]


class AgentTaskState(TypedDict, total=False):
    """Serializable state shared by Supervisor nodes."""

    run_id: str
    user_query: str
    intent: dict[str, Any]
    plan: dict[str, Any]
    task_status: TaskStatus
    sourcing_result: dict[str, Any]
    risk_result: dict[str, Any]
    compliance_result: dict[str, Any]
    sentiment_result: dict[str, Any]
    evidence: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    pending_approvals: list[dict[str, Any]]
    final_answer: str
    error: dict[str, Any] | None
