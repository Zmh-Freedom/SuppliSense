"""HTTP contracts for the unified sourcing workspace read model."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourcingTaskHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["agent_run", "legacy_request"]
    source_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    status: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)
    time_basis: Literal["updated_at", "created_at"]
    resume_url: str = Field(min_length=1, max_length=1000)


class SourcingTaskSourceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["available", "unavailable", "disabled"]
    reason: str | None = Field(default=None, max_length=255)


class SourcingTaskListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[SourcingTaskHistoryItem] = Field(default_factory=list, max_length=100)
    next_cursor: str | None = Field(default=None, max_length=4096)
    sources: dict[str, SourcingTaskSourceStatus]
