"""Response contracts for the procurement dashboard workbench."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class WorkbenchItem(BaseModel):
    case_id: str
    target_id: str
    title: str
    group: Literal["pending_closure", "overdue", "due_review", "high", "normal"]
    status: str
    owner_user_id: str | None = None
    due_at: datetime | None = None
    next_review_at: datetime | None = None
    detail_url: str


class WorkbenchCoverageTarget(BaseModel):
    target_id: str
    display_name: str
    missing_dimensions: list[str] = Field(default_factory=list)
    unknown: bool = False
    stale: bool = False
    failed: bool = False


class WorkbenchCoverage(BaseModel):
    target_total: int
    unknown_count: int
    stale_count: int
    failed_count: int
    targets: list[WorkbenchCoverageTarget] = Field(default_factory=list)


class WorkbenchSection(BaseModel):
    status: Literal["available", "unavailable"]
    count: int | None


class ProcurementWorkbenchResponse(BaseModel):
    scope: dict
    capabilities: dict
    counts: dict[str, int | None]
    items: list[WorkbenchItem]
    next_cursor: str | None
    coverage: WorkbenchCoverage | None
    sections: dict[str, WorkbenchSection]
