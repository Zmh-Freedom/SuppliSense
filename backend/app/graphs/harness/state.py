"""Serializable state contracts for the unified Agent Harness Runtime."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class ExecutionBudget(BaseModel):
    """Hard limits shared by every node in one Harness run."""

    model_config = ConfigDict(extra="forbid")

    max_llm_calls: int = Field(default=4, gt=0)
    max_tool_calls: int = Field(default=12, gt=0)
    max_loop_iterations: int = Field(default=2, ge=0)
    max_duration_seconds: int = Field(default=120, gt=0)
    max_parallel_tasks: int = Field(default=4, gt=0)


class HarnessTask(BaseModel):
    """One deterministic unit of work delegated to ToolExecutor."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    entity_id: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    required: bool = True
    evidence_requirements: list[str] = Field(default_factory=list)
    status: Literal["pending", "running", "completed", "partial", "failed"] = "pending"
    attempts: int = Field(default=0, ge=0)


class HarnessState(TypedDict, total=False):
    """State passed between LangGraph nodes.

    Lists contain JSON-compatible dictionaries so this state can be persisted by
    LangGraph checkpoints or PostgreSQL without leaking service objects.
    """

    schema_version: int
    session_id: str
    turn_id: str
    run_id: str
    user_id: str | None
    user_message: str
    execution_context: dict[str, Any]
    current_task: dict[str, Any]
    task_specs: list[dict[str, Any]]
    remediation_specs: list[dict[str, Any]]
    tool_outcomes: list[dict[str, Any]]
    evidence_records: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    validated_claims: list[dict[str, Any]]
    evidence_coverage: dict[str, Any]
    answer: dict[str, Any]
    budget: dict[str, Any]
    tool_call_count: int
    llm_call_count: int
    loop_iterations: int
    remediation_attempts: int
    status: str
    error: dict[str, Any] | None
    events: list[dict[str, Any]]
    started_at: str


def new_budget(value: dict[str, Any] | None = None) -> ExecutionBudget:
    """Validate an optional caller budget against the shared defaults."""
    return ExecutionBudget.model_validate(value or {})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["ExecutionBudget", "HarnessState", "HarnessTask", "new_budget", "utc_now_iso"]
