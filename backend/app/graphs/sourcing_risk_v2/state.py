"""State contract for the durable Sourcing Risk V2 graph."""

from __future__ import annotations

from typing import Any, TypedDict


class SourcingRiskGraphState(TypedDict, total=False):
    """Checkpointed orchestration state; business decisions remain in services."""

    run_id: str
    status: str
    requirement_id: str | None
    policy_snapshot_id: str | None
    candidate_ids: list[str]
    pending_review_ids: list[str]
    event_cursor: int
    error_code: str | None
    next_action: str | None
    requirement_input: dict[str, Any]
    requirement: dict[str, Any]
    policy_snapshot: dict[str, Any]
    candidates: list[dict[str, Any]]
    external_candidates: list[dict[str, Any]]
    evidence_by_company_id: dict[str, list[dict[str, Any]]]
    provider_failures: list[str]
    evidence_reviews: dict[str, dict[str, Any]]
    decisions: list[dict[str, Any]]
