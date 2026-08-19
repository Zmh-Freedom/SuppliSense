"""Versioned, serializable contracts shared by all Agent graphs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TaskType = Literal["sourcing", "analysis", "comparison", "action_draft"]
TaskStatus = Literal[
    "pending",
    "running",
    "completed",
    "partial",
    "needs_clarification",
    "waiting_approval",
    "failed",
]
SubtaskStatus = Literal[
    "pending",
    "running",
    "completed",
    "insufficient_evidence",
    "failed",
]
LoopType = Literal["sourcing", "evidence", "validation", "fallback"]


class SupplierReference(BaseModel):
    """A supplier entity retained across conversation turns."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    kind: str = "supplier"
    source: str | None = None
    discovery_source: str | None = None
    candidate_id: str | None = None
    candidate_type: str | None = None
    identity_status: str | None = None
    company_id: str | None = None
    website_url: str | None = None
    website_status: str | None = None
    website_url_source: str | None = None
    contact_phone: str | None = None
    contact_phone_source: str | None = None
    contact_email: str | None = None
    contact_email_source: str | None = None
    contact_status: str | None = None


class AgentSubtask(BaseModel):
    """One supplier and dimension unit of work in an Agent task."""

    subtask_id: str = Field(min_length=1)
    supplier_name: str | None = None
    dimension: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    required: bool = True
    evidence_requirements: list[str] = Field(default_factory=list)
    status: SubtaskStatus = "pending"
    attempts: int = Field(default=0, ge=0)
    evidence_refs: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None


class AgentTask(BaseModel):
    """A user-visible Agent task and its deterministic execution units."""

    task_id: str = Field(min_length=1)
    task_type: TaskType
    target_supplier_names: list[str] = Field(default_factory=list)
    analysis_dimensions: list[str] = Field(default_factory=list)
    requirement: dict[str, Any] | None = None
    user_message: str | None = None
    subtasks: list[AgentSubtask] = Field(default_factory=list)
    status: TaskStatus = "pending"

    @model_validator(mode="after")
    def validate_unique_subtasks(self) -> "AgentTask":
        pairs = [
            (subtask.supplier_name or "", subtask.dimension)
            for subtask in self.subtasks
        ]
        if len(pairs) != len(set(pairs)):
            raise ValueError("a task may contain only one subtask per supplier and dimension")
        subtask_ids = {subtask.subtask_id for subtask in self.subtasks}
        if len(subtask_ids) != len(self.subtasks):
            raise ValueError("a task may not contain duplicate subtask ids")
        dependencies = {subtask.subtask_id: subtask.depends_on for subtask in self.subtasks}
        for subtask_id, depends_on in dependencies.items():
            unknown = set(depends_on) - subtask_ids
            if unknown:
                raise ValueError(f"subtask {subtask_id} has unknown dependencies")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(subtask_id: str) -> None:
            if subtask_id in visiting:
                raise ValueError("subtask dependency cycle")
            if subtask_id in visited:
                return
            visiting.add(subtask_id)
            for dependency in dependencies[subtask_id]:
                visit(dependency)
            visiting.remove(subtask_id)
            visited.add(subtask_id)

        for subtask_id in subtask_ids:
            visit(subtask_id)
        return self


class LoopState(BaseModel):
    """Budget and termination details for a bounded Agent loop."""

    loop_type: LoopType
    iteration: int = Field(ge=0)
    max_iterations: int = Field(gt=0)
    tool_call_count: int = Field(ge=0)
    max_tool_calls: int = Field(gt=0)
    started_at: datetime
    timeout_seconds: int = Field(gt=0)
    previous_fingerprint: str | None = None
    evidence_count_before: int = Field(default=0, ge=0)
    stop_reason: str | None = None

    @model_validator(mode="after")
    def validate_budget(self) -> "LoopState":
        if self.iteration > self.max_iterations:
            raise ValueError("loop iteration exceeds its budget")
        if self.tool_call_count > self.max_tool_calls:
            raise ValueError("tool call count exceeds its budget")
        return self


class ConversationState(BaseModel):
    """The durable fact source shared by every Agent execution graph."""

    schema_version: int = Field(default=1, ge=1)
    session_id: str = ""
    active_suppliers: list[SupplierReference] = Field(default_factory=list)
    selected_supplier_names: list[str] = Field(default_factory=list)
    current_requirement: dict[str, Any] | None = None
    current_task: AgentTask | None = None
    recent_tasks: list[dict[str, Any]] = Field(default_factory=list)
    pending_clarification: dict[str, Any] | None = None
    pending_approvals: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def migrate_conversation_state(
    raw_state: dict[str, Any] | None,
    *,
    session_id: str = "",
) -> ConversationState:
    """Convert the legacy dict shape to the current state contract without data loss."""
    raw = raw_state if isinstance(raw_state, dict) else {}
    suppliers = [
        SupplierReference.model_validate(_normalize_reference(reference))
        for reference in raw.get("active_suppliers", [])
        if isinstance(reference, dict) and str(reference.get("name", "")).strip()
    ]
    selected_names = _string_items(raw.get("selected_supplier_names", raw.get("selected_suppliers", [])))
    if not selected_names:
        selected_names = _string_items(raw.get("current_task", {}).get("target_supplier_names", []))

    task = _migrate_task(raw.get("current_task"), selected_names)
    return ConversationState(
        schema_version=int(raw.get("schema_version", 1) or 1),
        session_id=str(raw.get("session_id") or session_id),
        active_suppliers=suppliers,
        selected_supplier_names=selected_names,
        current_requirement=raw.get("current_requirement"),
        current_task=task,
        recent_tasks=[item for item in raw.get("recent_tasks", []) if isinstance(item, dict)],
        pending_clarification=_dict_or_none(raw.get("pending_clarification")),
        pending_approvals=_string_items(raw.get("pending_approvals", [])),
        updated_at=raw.get("updated_at") or datetime.now(timezone.utc),
    )


def _migrate_task(raw_task: Any, selected_names: list[str]) -> AgentTask | None:
    if not isinstance(raw_task, dict):
        return None
    dimensions = _string_items(raw_task.get("analysis_dimensions", []))
    task_type: TaskType = "analysis" if dimensions or selected_names else "sourcing"
    return AgentTask(
        task_id=str(raw_task.get("task_id") or "legacy-current-task"),
        task_type=raw_task.get("task_type") or task_type,
        target_supplier_names=_string_items(raw_task.get("target_supplier_names", selected_names)),
        analysis_dimensions=dimensions,
        requirement=_dict_or_none(raw_task.get("requirement")),
        user_message=raw_task.get("user_message"),
        subtasks=[item for item in raw_task.get("subtasks", []) if isinstance(item, dict)],
        status=raw_task.get("status") or "pending",
    )


def _normalize_reference(reference: dict[str, Any]) -> dict[str, Any]:
    normalized = {**reference, "name": str(reference["name"]).strip()}
    if not normalized.get("discovery_source") and normalized.get("source"):
        normalized["discovery_source"] = normalized["source"]
    return normalized


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None
