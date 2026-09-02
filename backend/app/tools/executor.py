"""Central, fail-closed execution middleware for Agent tools."""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.logging import get_logger
from app.tools.registry import ToolRegistry

logger = get_logger()


class ToolContext(BaseModel):
    """Per-call authorization, budget and tracing context."""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str | None = None
    run_id: str | None = None
    user_id: str | None = None
    approval_token: str | None = None
    idempotency_key: str | None = None
    tool_call_count: int = Field(default=0, ge=0)
    max_tool_calls: int = Field(default=32, gt=0)
    timeout_seconds: int | None = Field(default=None, gt=0)
    allowed_tools: set[str] | None = None


class ToolError(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    message: str
    retryable: bool = False


class ToolMetrics(BaseModel):
    attempts: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)


class ToolOutcome(BaseModel):
    """Stable envelope consumed by graphs, streaming and answer validation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    call_id: str
    tool_name: str
    tool_version: str
    status: Literal[
        "success", "partial", "not_found", "unavailable", "invalid", "denied", "failed"
    ]
    data: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    error: ToolError | None = None
    side_effect_receipt: dict[str, Any] | None = None
    metrics: ToolMetrics = Field(default_factory=ToolMetrics)


class ToolExecutor:
    """Execute registered tools with validation, policy and bounded retries."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        call_recorder: Callable[[ToolOutcome], Awaitable[None] | None] | None = None,
    ) -> None:
        self.registry = registry
        self.call_recorder = call_recorder

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolOutcome:
        context = context or ToolContext()
        definition = self.registry.get(tool_name)
        if definition is None:
            outcome = self._outcome(context, tool_name, "invalid", error=("unknown_tool", "工具未注册", False))
            await self._record(outcome)
            return outcome
        spec = definition.spec
        if context.allowed_tools is not None and tool_name not in context.allowed_tools:
            outcome = self._outcome(context, tool_name, "denied", error=("tool_not_allowed", "当前会话无权调用该工具", False), version=spec.version)
            await self._record(outcome)
            return outcome
        if context.tool_call_count >= context.max_tool_calls:
            outcome = self._outcome(context, tool_name, "denied", error=("tool_budget_exhausted", "工具调用预算已用尽", False), version=spec.version)
            await self._record(outcome)
            return outcome
        if spec.side_effect == "write":
            if spec.approval_policy == "required" and not context.approval_token:
                outcome = self._outcome(context, tool_name, "denied", error=("approval_required", "写操作必须先获得人工审批令牌", False), version=spec.version)
                await self._record(outcome)
                return outcome
            if not context.idempotency_key:
                outcome = self._outcome(context, tool_name, "invalid", error=("idempotency_key_required", "写操作必须提供幂等键", False), version=spec.version)
                await self._record(outcome)
                return outcome

        try:
            validated = definition.input_model.model_validate(arguments)
        except Exception as exc:
            outcome = self._outcome(context, tool_name, "invalid", error=("invalid_input", str(exc), False), version=spec.version)
            await self._record(outcome)
            return outcome

        attempts = 0
        started = time.monotonic()
        raw_result: Any = None
        last_error: ToolError | None = None
        max_attempts = spec.max_attempts
        timeout = context.timeout_seconds or spec.timeout_seconds
        while attempts < max_attempts:
            attempts += 1
            try:
                raw_result = await asyncio.wait_for(
                    self._invoke(definition.tool, validated.model_dump()), timeout=timeout
                )
                break
            except asyncio.TimeoutError:
                last_error = ToolError(code="timeout", message="工具执行超时", retryable=True)
            except Exception as exc:
                last_error = ToolError(code="tool_execution_failed", message=str(exc), retryable=attempts < max_attempts)
            if last_error and not last_error.retryable:
                break

        duration_ms = int((time.monotonic() - started) * 1000)
        if last_error and raw_result is None:
            outcome = self._outcome(
                context,
                tool_name,
                "unavailable" if last_error.code == "timeout" else "failed",
                error=(last_error.code, last_error.message, last_error.retryable),
                version=spec.version,
                attempts=attempts,
                duration_ms=duration_ms,
            )
            await self._record(outcome)
            return outcome
        try:
            payload = definition.output_model.model_validate(raw_result)
        except Exception as exc:
            outcome = self._outcome(
                context,
                tool_name,
                "invalid",
                error=("invalid_output", str(exc), False),
                version=spec.version,
                attempts=attempts,
                duration_ms=duration_ms,
            )
            await self._record(outcome)
            return outcome

        data = payload.model_dump(mode="json")
        status = _status_from_payload(data)
        outcome = self._outcome(
            context,
            tool_name,
            status,
            data=data,
            evidence_refs=_string_list(data.get("evidence_refs")),
            side_effect_receipt=data.get("side_effect_receipt") or data.get("receipt"),
            version=spec.version,
            attempts=attempts,
            duration_ms=duration_ms,
        )
        await self._record(outcome)
        return outcome

    async def _invoke(self, tool: Any, arguments: dict[str, Any]) -> Any:
        if inspect.iscoroutinefunction(getattr(tool, "ainvoke", None)):
            return await tool.ainvoke(arguments)
        return await asyncio.to_thread(tool.invoke, arguments)

    async def _record(self, outcome: ToolOutcome) -> None:
        if not self.call_recorder:
            return
        result = self.call_recorder(outcome)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _outcome(
        context: ToolContext,
        tool_name: str,
        status: Literal["success", "partial", "not_found", "unavailable", "invalid", "denied", "failed"],
        *,
        data: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
        error: tuple[str, str, bool] | None = None,
        side_effect_receipt: dict[str, Any] | None = None,
        version: str = "unknown",
        attempts: int = 0,
        duration_ms: int = 0,
    ) -> ToolOutcome:
        return ToolOutcome(
            call_id=context.call_id,
            tool_name=tool_name,
            tool_version=version,
            status=status,
            data=data or {},
            evidence_refs=evidence_refs or [],
            error=ToolError(code=error[0], message=error[1], retryable=error[2]) if error else None,
            side_effect_receipt=side_effect_receipt,
            metrics=ToolMetrics(attempts=attempts, duration_ms=duration_ms),
        )


def _status_from_payload(payload: dict[str, Any]) -> Literal["success", "partial", "not_found", "unavailable", "invalid", "denied", "failed"]:
    declared = str(payload.get("status") or "").lower()
    if declared in {"partial", "not_found", "unavailable", "invalid", "denied", "failed", "success"}:
        return declared  # type: ignore[return-value]
    if payload.get("cancelled") or payload.get("error") in {"approval_required", "approval_context_required"}:
        return "denied"
    if payload.get("error"):
        return "failed"
    return "success"


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []


__all__ = ["ToolContext", "ToolError", "ToolExecutor", "ToolMetrics", "ToolOutcome"]
