"""Pydantic contracts for procurement cases and their commands."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CaseType = Literal["risk_review", "coverage_gap", "identity_review"]
ClosureCategory = Literal["resolved", "accepted_limitation", "duplicate", "withdrawn"]


class CaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monitor_target_id: str = Field(min_length=1, max_length=128)
    case_type: CaseType
    department_code: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=2000)
    priority: Literal["high", "normal"] = "normal"
    due_at: datetime | None = None


class CaseCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: Literal[
        "claim",
        "assign",
        "request_transfer",
        "receipt",
        "defer",
        "resume",
        "set_due",
        "request_closure",
        "approve_closure",
        "reject_closure",
        "reopen",
    ]
    expected_version: int = Field(ge=1)
    owner_user_id: str | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=2000)
    summary: str | None = Field(default=None, min_length=1, max_length=2000)
    refs: list[str] | None = Field(default=None, max_length=100)
    next_review_at: datetime | None = None
    due_at: datetime | None = None
    category: ClosureCategory | None = None
    receipt_ids: list[str] | None = Field(default=None, max_length=100)
    residual_limitations: str | None = Field(default=None, min_length=1, max_length=2000)
    duplicate_case_id: str | None = None

    @model_validator(mode="after")
    def validate_command_fields(self) -> CaseCommandRequest:
        required: dict[str, set[str]] = {
            "claim": set(),
            "assign": {"owner_user_id"},
            "request_transfer": {"reason"},
            "receipt": {"summary", "refs"},
            "defer": {"reason", "next_review_at"},
            "resume": set(),
            "set_due": {"due_at"},
            "request_closure": {"category", "summary", "receipt_ids"},
            "approve_closure": {"reason"},
            "reject_closure": {"reason"},
            "reopen": {"reason"},
        }
        allowed = {"command", "expected_version", *required[self.command]}
        if self.command == "request_closure":
            allowed.update({"residual_limitations", "duplicate_case_id"})
        provided = self.model_fields_set
        unexpected = provided - allowed
        if unexpected:
            raise ValueError(f"该命令不接受字段：{', '.join(sorted(unexpected))}")
        missing = required[self.command] - provided
        if missing:
            raise ValueError(f"命令缺少必需字段：{', '.join(sorted(missing))}")
        for field_name in ("reason", "summary", "residual_limitations"):
            value = getattr(self, field_name)
            if field_name in provided and (value is None or not value.strip()):
                raise ValueError(f"{field_name}不能为空")
        if self.command == "request_closure":
            if self.category == "duplicate" and not self.duplicate_case_id:
                raise ValueError("duplicate 关闭申请必须指定目标事项")
            if self.category != "duplicate" and self.duplicate_case_id is not None:
                raise ValueError("仅 duplicate 关闭申请接受 duplicate_case_id")
            if self.category != "accepted_limitation" and self.residual_limitations is not None:
                raise ValueError("仅 accepted_limitation 接受 residual_limitations")
        return self


class CaseReviewTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: Literal[
        "verify_identity", "assess", "review", "supplement_data", "continue_monitoring"
    ]
    payload: dict = Field(default_factory=dict)
