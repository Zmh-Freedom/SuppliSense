from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class OutboxReplayInput(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class OutboxEventResponse(BaseModel):
    event_id: UUID
    event_type: str
    aggregate_type: str
    aggregate_id: str
    schema_version: int
    occurred_at: datetime
    published_at: datetime | None
    attempt_count: int
    last_error: str | None
    next_attempt_at: datetime
    dead_lettered_at: datetime | None


class OutboxReplayResponse(BaseModel):
    event_id: UUID
    status: Literal["queued"]
    reason: str
