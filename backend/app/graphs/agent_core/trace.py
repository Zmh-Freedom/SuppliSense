"""Stable, user-understandable Agent trace events without model reasoning."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


TRACE_EVENT_TYPE = "agent_trace"
TRACE_SCHEMA_VERSION = 1
_PRIVATE_KEYS = frozenset({
    "chain_of_thought", "cot", "reasoning", "thought", "thoughts",
    "analysis", "prompt", "system_prompt", "messages", "raw_response",
    "model_output",
})


class AgentTraceEvent(BaseModel):
    """A display-safe lifecycle observation persisted in ``agent_run_events``."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = TRACE_SCHEMA_VERSION
    kind: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=200)
    data: dict[str, Any] = Field(default_factory=dict)


def build_agent_trace_events(
    event_type: str,
    task_status: str,
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert durable Supervisor snapshots into public-safe lifecycle events."""
    if event_type == "stage":
        return [_trace("state_parsed", task_status, "已解析任务执行状态", {
            "stage": _text(snapshot.get("stage")),
        })]
    if event_type == "planning":
        tasks = _tasks(snapshot)
        agents = sorted({str(task.get("agent")) for task in tasks if task.get("agent")})
        analysis_scope = _mapping(snapshot.get("analysis_scope"))
        return [
            _trace("route_selected", task_status, "已选择 Supervisor 编排路径", {
                "route": "supervisor", "agent_count": len(agents), "agents": agents,
            }),
            _trace("plan_created", task_status, "已生成可执行任务计划", {
                "task_count": len(tasks), "task_ids": _task_ids(tasks),
                "target_supplier_names": _text_list(
                    analysis_scope.get("target_supplier_names")
                ),
                "analysis_dimensions": _text_list(
                    analysis_scope.get("analysis_dimensions")
                ),
            }),
        ]
    if event_type == "agent_start":
        return [_trace("subtask_started", task_status, "已开始执行子任务", {
            "task_id": _text(snapshot.get("task_id")),
            "agent": _text(snapshot.get("agent")),
        })]
    if event_type in {"agent_result", "evidence_remediation_result"}:
        result = _mapping(snapshot.get("result"))
        status = _text(result.get("status"), "unknown")
        metrics = _mapping(result.get("metrics"))
        base = {
            "task_id": _text(snapshot.get("task_id")),
            "agent": _text(result.get("agent")),
            "result_status": status,
        }
        lifecycle = "subtask_completed" if status == "completed" else "subtask_failed"
        message = "子任务已完成" if status == "completed" else "子任务未完成"
        return [
            _trace(lifecycle, task_status, message, base),
            _trace("tool_summary", task_status, "已汇总工具执行结果", {
                **base,
                "duration_ms": _non_negative_int(metrics.get("duration_ms")),
                "attempts": _non_negative_int(metrics.get("attempts")),
                "evidence_count": _non_negative_int(metrics.get("evidence_count")),
            }),
        ]
    if event_type == "evidence_merge":
        validation = _mapping(snapshot.get("evidence_validation"))
        loop = _mapping(snapshot.get("evidence_loop"))
        merge = _mapping(snapshot.get("evidence_merge"))
        evidence = merge.get("evidence") if isinstance(merge.get("evidence"), list) else []
        refs = [
            str(item.get("evidence_id"))
            for item in evidence if isinstance(item, dict) and item.get("evidence_id")
        ]
        return [
            _trace("loop_evaluated", task_status, "已评估证据补全循环", {
                "iteration": _non_negative_int(loop.get("iteration")),
                "max_iterations": _non_negative_int(loop.get("max_iterations")),
                "tool_call_count": _non_negative_int(loop.get("tool_call_count")),
                "stop_reason": _text(loop.get("stop_reason")),
            }),
            _trace("validator_completed", task_status, "已完成证据校验", {
                "validation_status": _text(validation.get("status")),
                "coverage": _number(validation.get("coverage")),
                "can_recommend": validation.get("can_recommend") is True,
                "stop_reason": _text(validation.get("stop_reason")),
            }),
            _trace("evidence_referenced", task_status, "已关联可复核证据", {
                "evidence_count": len(refs), "evidence_ids": refs[:50],
            }),
        ]
    if event_type == "approval_required":
        approvals = _approvals(snapshot)
        return [_trace("approval_required", task_status, "检测到需人工确认的操作", {
            "approval_count": len(approvals),
            "action_types": _action_types(approvals),
        })]
    if event_type == "approval":
        return [_trace("approval_resolved", task_status, "人工审批结果已记录", {
            "approved": snapshot.get("approved") is True,
            "approval_status": _text(snapshot.get("status")),
            "approval_count": len(_approvals(snapshot)),
        })]
    if event_type == "done":
        kind = "run_completed" if task_status == "COMPLETED" else "run_partial"
        message = "任务已完成" if kind == "run_completed" else "任务以部分结果结束"
        return [_trace(kind, task_status, message, {"final_status": task_status})]
    return []


def sanitize_graph_trace_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep legacy V2 trace useful while excluding node output and model internals."""
    allowed = {
        "run_id", "status", "stage", "node", "source", "count", "sufficient",
        "checksum", "provider", "error", "failed_dimensions", "requires_review",
        "pending_review_ids", "resume",
    }
    return {
        key: _safe_value(value)
        for key, value in payload.items()
        if key in allowed and key not in _PRIVATE_KEYS
    }


def _trace(kind: str, status: str, message: str, data: dict[str, Any]) -> dict[str, Any]:
    return AgentTraceEvent(
        kind=kind,
        status=status,
        message=message,
        data={key: _safe_value(value) for key, value in data.items()},
    ).model_dump(mode="json")


def _tasks(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    plan = _mapping(snapshot.get("plan"))
    tasks = snapshot.get("task_matrix", plan.get("tasks"))
    return [item for item in tasks if isinstance(item, dict)] if isinstance(tasks, list) else []


def _task_ids(tasks: list[dict[str, Any]]) -> list[str]:
    return [str(task.get("task_id") or task.get("subtask_id")) for task in tasks if task.get("task_id") or task.get("subtask_id")]


def _text_list(value: Any) -> list[str]:
    return [str(item) for item in value if isinstance(item, (str, int, float, bool))] if isinstance(value, list) else []


def _approvals(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    approvals = snapshot.get("pending_approvals")
    return [item for item in approvals if isinstance(item, dict)] if isinstance(approvals, list) else []


def _action_types(approvals: list[dict[str, Any]]) -> list[str]:
    return sorted({str(item.get("action_type")) for item in approvals if item.get("action_type")})


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any, default: str = "") -> str:
    return str(value) if isinstance(value, (str, int, float, bool)) else default


def _non_negative_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and value >= 0 else 0


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if str(key).lower() not in _PRIVATE_KEYS
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
