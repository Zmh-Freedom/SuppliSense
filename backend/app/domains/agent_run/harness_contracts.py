"""Durable contracts shared by the Agent Harness control plane.

These contracts describe control-plane state only.  Domain evidence and
conversation display payloads remain owned by MongoDB and are referenced by
IDs or snapshots from the control plane.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SessionStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ARCHIVED = "archived"


class TurnStatus(str, Enum):
    RECEIVED = "received"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EntityStatus(str, Enum):
    PENDING_VERIFICATION = "pending_verification"
    RESOLVED = "resolved"
    RETIRED = "retired"


class ToolCallStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    REJECTED = "rejected"


class HarnessActionProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class _ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _deep_freeze(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_freeze(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(item) for item in value)
    return value


class AgentSession(_ImmutableModel):
    id: UUID
    user_id: UUID | None = None
    status: SessionStatus = SessionStatus.ACTIVE
    version: int = Field(default=1, ge=1)
    state: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("state")
    @classmethod
    def freeze_state(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _deep_freeze(value)


class AgentTurn(_ImmutableModel):
    id: UUID
    session_id: UUID
    turn_number: int = Field(ge=1)
    user_message: str = Field(min_length=1, max_length=12000)
    assistant_message: str | None = Field(default=None, max_length=30000)
    status: TurnStatus = TurnStatus.RECEIVED
    request: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] | None = None
    run_id: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    @field_validator("request", "response")
    @classmethod
    def freeze_payload(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _deep_freeze(value) if value is not None else None


class AgentRun(_ImmutableModel):
    id: UUID
    session_id: UUID | None = None
    user_id: UUID | None = None
    run_type: str = Field(min_length=1, max_length=32)
    status: str = Field(min_length=1, max_length=32)
    version: int = Field(default=1, ge=1)
    state: dict[str, Any] = Field(default_factory=dict)
    policy_snapshot_id: UUID | None = None
    decision_id: UUID | None = None
    error_code: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    @field_validator("state")
    @classmethod
    def freeze_state(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _deep_freeze(value)


class AgentTask(_ImmutableModel):
    id: UUID
    session_id: UUID
    turn_id: UUID | None = None
    run_id: UUID | None = None
    task_type: str = Field(min_length=1, max_length=64)
    status: str = Field(default="pending", min_length=1, max_length=32)
    version: int = Field(default=1, ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("payload")
    @classmethod
    def freeze_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _deep_freeze(value)


class AgentEntity(_ImmutableModel):
    id: UUID
    session_id: UUID
    entity_key: str = Field(min_length=1, max_length=255)
    entity_type: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    canonical_id: str | None = None
    status: EntityStatus = EntityStatus.PENDING_VERIFICATION
    mention_count: int = Field(default=1, ge=1)
    focus_rank: int | None = Field(default=None, ge=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("attributes")
    @classmethod
    def freeze_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _deep_freeze(value)


class AgentToolCall(_ImmutableModel):
    id: UUID
    session_id: UUID
    run_id: UUID | None = None
    task_id: UUID | None = None
    tool_name: str = Field(min_length=1, max_length=128)
    status: ToolCallStatus = ToolCallStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    idempotency_key: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    @field_validator("input", "output", "error")
    @classmethod
    def freeze_payload(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _deep_freeze(value) if value is not None else None


class AgentActionProposal(_ImmutableModel):
    id: UUID
    session_id: UUID | None = None
    run_id: UUID
    candidate_id: UUID | None = None
    action_type: str = Field(min_length=1, max_length=64)
    status: HarnessActionProposalStatus = HarnessActionProposalStatus.PENDING
    execution_state: str = Field(default="pending", min_length=1, max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("payload")
    @classmethod
    def freeze_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _deep_freeze(value)


class SessionTurnCommit(_ImmutableModel):
    session: AgentSession
    turn: AgentTurn


__all__ = [
    "AgentActionProposal",
    "AgentEntity",
    "AgentRun",
    "AgentSession",
    "AgentTask",
    "AgentToolCall",
    "AgentTurn",
    "EntityStatus",
    "HarnessActionProposalStatus",
    "SessionStatus",
    "SessionTurnCommit",
    "ToolCallStatus",
    "TurnStatus",
]
