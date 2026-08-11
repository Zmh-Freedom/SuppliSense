from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class OutboxReplayInput(BaseModel):
    reason: str = Field(min_length=2, max_length=500)

    @field_validator("reason")
    @classmethod
    def require_non_blank_reason(cls, value: str) -> str:
        reason = value.strip()
        if len(reason) < 2:
            raise ValueError("回放原因至少需要 2 个字符")
        return reason


class OutboxAdminEventResponse(BaseModel):
    """Safe projection of an Outbox event for administrator operations."""

    event_id: UUID
    event_type: str
    aggregate_type: str
    aggregate_id: str
    schema_version: int
    status: Literal["pending", "failed", "dead_letter", "published"]
    attempt_count: int
    occurred_at: datetime
    published_at: datetime | None
    last_error: str | None


class OutboxEventListResponse(BaseModel):
    events: list[OutboxAdminEventResponse]


class OutboxReplayResponse(BaseModel):
    event_id: UUID
    status: Literal["queued"]
    reason: str
