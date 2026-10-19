"""Graph checkpoint verification against the PostgreSQL execution ledger."""

import hashlib
import json
from typing import Any

from app.domains.agent_run.coordinator import ExecutionLost


def input_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def checkpoint_proof(checkpoint: Any) -> dict[str, Any]:
    config = dict(checkpoint.config or {})
    return {"checkpoint_id": (config.get("configurable") or {}).get("checkpoint_id"),
            "digest": input_digest(dict(checkpoint.values or {})), "config": config}


def validate_checkpoint(record: dict[str, Any], values: dict[str, Any]) -> None:
    frozen = record["execution_input"]
    for key in ("run_id", "session_id", "user_id"):
        if str(values.get(key) or "") != str(frozen.get(key) or ""):
            raise ExecutionLost("检查点身份与冻结输入不一致，需要人工核对")
    if values.get("input_digest") != input_digest(frozen):
        raise ExecutionLost("检查点输入摘要不一致，需要人工核对")
    ledger = record.get("snapshot") or {}
    # A PG callback can commit before the saver. Never replay that uncertain
    # window: keep the Run paused rather than discard an observed result.
    for key in ("plan", "agent_results", "pending_approvals", "action_receipts",
                "task_specs", "tool_outcomes", "evidence_records", "budget",
                "tool_call_count", "llm_call_count", "loop_iterations", "started_at"):
        if key in ledger and ledger[key] != values.get(key):
            raise ExecutionLost(f"检查点与已提交的 {key} 不一致，需要人工核对")


def reconcile_checkpoint(record: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Restore committed read results when the saver trails the domain commit."""
    if record["execution_input"].get("runtime") != "harness":
        validate_checkpoint(record, values)
        return values
    ledger = dict(record.get("snapshot") or {})
    validate_checkpoint({**record, "snapshot": {}}, values)
    if not ledger:
        return {**values, "tool_call_count": max(int(values.get("tool_call_count", 0)), int(record.get("reserved_tool_calls", 0)))}
    ledger["tool_call_count"] = max(int(ledger.get("tool_call_count", 0)), int(record.get("reserved_tool_calls", 0)))
    validate_checkpoint({**record, "snapshot": {}}, {**values, **ledger})
    def plan(tasks):
        return {task["task_id"]: {key: value for key, value in task.items() if key not in {"status", "attempts"}} for task in tasks}
    if "task_specs" in ledger and plan(ledger["task_specs"]) != plan(values.get("task_specs") or []):
        raise ExecutionLost("检查点与已提交的任务计划不一致，需要人工核对")
    for key in ("tool_call_count", "llm_call_count", "loop_iterations"):
        if int(ledger.get(key, 0)) < int(values.get(key, 0)):
            raise ExecutionLost("检查点消耗超过已提交预算，需要人工核对")
    if "budget" in ledger and "budget" in values:
        from app.graphs.harness.state import new_budget
        if new_budget(ledger["budget"]) != new_budget(values["budget"]):
            raise ExecutionLost("检查点预算与已提交政策不一致")
    return {**values, **ledger}
