from enum import Enum


class AgentRunStatus(str, Enum):
    CREATED = "CREATED"
    CLARIFYING = "CLARIFYING"
    POLICY_LOCKED = "POLICY_LOCKED"
    LOCAL_SEARCHING = "LOCAL_SEARCHING"
    EXTERNAL_REVIEW = "EXTERNAL_REVIEW"
    IDENTITY_RESOLVING = "IDENTITY_RESOLVING"
    IDENTITY_REVIEW = "IDENTITY_REVIEW"
    INVESTIGATING = "INVESTIGATING"
    EVIDENCE_REVIEW = "EVIDENCE_REVIEW"
    SCORING = "SCORING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    ACTION_PENDING = "ACTION_PENDING"
    ACTION_EXECUTING = "ACTION_EXECUTING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    ACTION_FAILED = "ACTION_FAILED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ROLLBACK_FROZEN = "ROLLBACK_FROZEN"


class CandidateSource(str, Enum):
    LOCAL = "local"
    STAGED_EXTERNAL = "staged_external"


class CandidateStatus(str, Enum):
    STAGED_CANDIDATE = "staged_candidate"
    IDENTITY_PENDING = "identity_pending"
    IDENTITY_CONFIRMED = "identity_confirmed"
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_REVIEW = "needs_review"


class ActionProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


ALLOWED_STATUS_TRANSITIONS = {
    status: frozenset() for status in AgentRunStatus
}
ALLOWED_STATUS_TRANSITIONS.update({
    AgentRunStatus.CREATED: frozenset({AgentRunStatus.CLARIFYING, AgentRunStatus.POLICY_LOCKED, AgentRunStatus.CANCELLED}),
    AgentRunStatus.CLARIFYING: frozenset({AgentRunStatus.CREATED, AgentRunStatus.POLICY_LOCKED, AgentRunStatus.CANCELLED}),
    AgentRunStatus.POLICY_LOCKED: frozenset({AgentRunStatus.LOCAL_SEARCHING, AgentRunStatus.CANCELLED, AgentRunStatus.FAILED}),
    AgentRunStatus.LOCAL_SEARCHING: frozenset({AgentRunStatus.EXTERNAL_REVIEW, AgentRunStatus.IDENTITY_RESOLVING, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}),
    AgentRunStatus.EXTERNAL_REVIEW: frozenset({AgentRunStatus.IDENTITY_RESOLVING, AgentRunStatus.LOCAL_SEARCHING, AgentRunStatus.CANCELLED}),
    AgentRunStatus.IDENTITY_RESOLVING: frozenset({AgentRunStatus.IDENTITY_REVIEW, AgentRunStatus.INVESTIGATING, AgentRunStatus.NEEDS_REVIEW, AgentRunStatus.CANCELLED}),
    AgentRunStatus.IDENTITY_REVIEW: frozenset({AgentRunStatus.IDENTITY_RESOLVING, AgentRunStatus.INVESTIGATING, AgentRunStatus.NEEDS_REVIEW, AgentRunStatus.CANCELLED}),
    AgentRunStatus.INVESTIGATING: frozenset({AgentRunStatus.EVIDENCE_REVIEW, AgentRunStatus.SCORING, AgentRunStatus.PARTIAL, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}),
    AgentRunStatus.EVIDENCE_REVIEW: frozenset({AgentRunStatus.INVESTIGATING, AgentRunStatus.SCORING, AgentRunStatus.NEEDS_REVIEW, AgentRunStatus.PARTIAL, AgentRunStatus.CANCELLED}),
    AgentRunStatus.SCORING: frozenset({AgentRunStatus.READY_FOR_REVIEW, AgentRunStatus.NEEDS_REVIEW, AgentRunStatus.PARTIAL, AgentRunStatus.FAILED}),
    AgentRunStatus.READY_FOR_REVIEW: frozenset({AgentRunStatus.ACTION_PENDING, AgentRunStatus.COMPLETED, AgentRunStatus.PARTIAL, AgentRunStatus.NEEDS_REVIEW, AgentRunStatus.CANCELLED}),
    AgentRunStatus.ACTION_PENDING: frozenset({AgentRunStatus.ACTION_EXECUTING, AgentRunStatus.READY_FOR_REVIEW, AgentRunStatus.CANCELLED}),
    AgentRunStatus.ACTION_EXECUTING: frozenset({AgentRunStatus.COMPLETED, AgentRunStatus.ACTION_FAILED, AgentRunStatus.PARTIAL}),
    AgentRunStatus.ROLLBACK_FROZEN: frozenset(),
})
