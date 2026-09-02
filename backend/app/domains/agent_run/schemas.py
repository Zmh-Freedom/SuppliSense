from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.agent_run.models import AgentRunStatus


class _FrozenDict(dict[str, Any]):
    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("immutable payload")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable


class _FrozenList(list[Any]):
    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("immutable payload")

    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = _immutable


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return _FrozenDict({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_deep_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(item) for item in value)
    return value


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

    @field_validator("answers")
    @classmethod
    def freeze_answers(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _deep_freeze(value)


class IdentityResolutionRequest(_ImmutableModel):
    expected_version: int = Field(ge=1)
    resolutions: dict[str, str] = Field(min_length=1)

    @field_validator("resolutions")
    @classmethod
    def validate_resolutions(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not review_id.strip() or not company_id.strip() for review_id, company_id in value.items()):
            raise ValueError("身份审核结果不能为空")
        return _deep_freeze(value)


class ApprovalDecisionRequest(_ImmutableModel):
    expected_version: int = Field(ge=1)
    decision: Literal["approved", "rejected"]
    comment: str | None = Field(default=None, max_length=2000)
    approval_token: str | None = Field(default=None, min_length=1, max_length=4096)


class CancelRunRequest(_ImmutableModel):
    expected_version: int = Field(ge=1)


class AgentRunResponse(_ImmutableModel):
    id: UUID
    run_id: UUID
    status: AgentRunStatus
    version: int = Field(ge=1)
    requirement: CreateSourcingRiskRunRequest
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    evidence_by_company_id: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    evidence_reviews: dict[str, dict[str, Any]] = Field(default_factory=dict)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    action_proposals: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    raw_payload_statuses: list[dict[str, str]] = Field(default_factory=list)

    @field_validator("candidates")
    @classmethod
    def freeze_candidates(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _deep_freeze(value)


class AgentRunEventResponse(_ImmutableModel):
    event_id: int = Field(ge=1)
    run_id: UUID
    version: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=64)
    occurred_at: datetime
    data: dict[str, Any]

    @field_validator("data")
    @classmethod
    def freeze_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _deep_freeze(value)


class AgentExecutionSnapshot(_ImmutableModel):
    """Internal recovery payload stored inside the existing run event stream."""

    task_matrix: list[dict[str, Any]] = Field(default_factory=list)
    subtask_results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    loops: dict[str, dict[str, Any]] = Field(default_factory=dict)
    validators: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pending_approvals: list[dict[str, Any]] = Field(default_factory=list)
