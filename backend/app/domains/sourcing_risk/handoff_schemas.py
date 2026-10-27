"""HTTP contracts for sourcing candidate handoffs into Chat."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CandidateHandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    candidate_key: UUID
    expected_version: int = Field(ge=1)
    intent: Literal["risk_review", "monitor_proposal"]


class CandidateHandoffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_id: UUID
    session_id: UUID
    chat_url: str = Field(min_length=1, max_length=1000)


class CandidateHandoffDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_id: UUID
    session_id: UUID
    source_run_id: UUID
    source_run_version: int = Field(ge=1)
    candidate_key: UUID
    candidate: dict[str, Any]
    requirement: dict[str, Any]
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    intent: Literal["risk_review", "monitor_proposal"]
    status: Literal["ready", "consumed", "cancelled"]
    continue_message: str = Field(min_length=1, max_length=2000)
    consumed_turn_id: UUID | None = None
    run_id: UUID | None = None
