from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.agent_run.models import AgentRunStatus


class _ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class CreateSourcingRiskRunRequest(_ImmutableModel):
    requirement_text: str = Field(min_length=1, max_length=4000)
    category: str | None = Field(default=None, max_length=128)
    specification: str | None = Field(default=None, max_length=2000)
    expected_candidate_count: int = Field(default=3, ge=1, le=20)

    @field_validator("category")
    @classmethod
    def reject_multi_category(cls, value: str | None) -> str | None:
        if value and any(separator in value for separator in ("、", ",", "，", "/")):
            raise ValueError("一个任务只能包含一个采购品类")
        return value.strip() if value else value


class ClarificationRequest(_ImmutableModel):
    expected_version: int = Field(ge=1)
    answers: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionRequest(_ImmutableModel):
    expected_version: int = Field(ge=1)
    decision: Literal["approved", "rejected"]
    comment: str | None = Field(default=None, max_length=2000)


class AgentRunResponse(_ImmutableModel):
    run_id: UUID
    status: AgentRunStatus
    version: int = Field(ge=1)
    requirement: CreateSourcingRiskRunRequest
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class AgentRunEventResponse(_ImmutableModel):
    event_id: int = Field(ge=1)
    run_id: UUID
    version: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=64)
    occurred_at: datetime
    data: dict[str, Any]
