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
        restored = {
            **values,
            "tool_call_count": max(
                int(values.get("tool_call_count", 0)),
                int(record.get("reserved_tool_calls", 0)),
            ),
        }
        return _restore_committed_tool_results(record, restored)
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
    return _restore_committed_tool_results(record, {**values, **ledger})


def _restore_committed_tool_results(
    record: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    """Merge durable tool results that landed before the node snapshot."""
    events = record.get("committed_tool_events") or []
    if not events:
        return values

    from app.graphs.harness.state import HarnessTask
    from app.graphs.harness.graph import _payload_claims, _payload_evidence
    from app.tools.executor import ToolOutcome

    task_specs = [dict(item) for item in values.get("task_specs", []) if isinstance(item, dict)]
    tasks_by_id = {str(task.get("task_id") or ""): task for task in task_specs}
    calls_by_task: dict[str, list[dict[str, Any]]] = {}
    result_events: list[dict[str, Any]] = []
    run_id = str(record["execution_input"].get("run_id") or "")

    for event in events:
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            raise ExecutionLost("已提交工具事件格式无效，需要人工核对")
        task_id = str(payload.get("task_id") or "")
        tool_name = str(payload.get("tool") or "")
        if not task_id or not tool_name:
            if event_type == "tool_result":
                raise ExecutionLost("已提交工具结果缺少任务身份，需要人工核对")
            continue
        if event_type == "tool_call":
            calls_by_task.setdefault(task_id, []).append({
                "event_id": int(event.get("event_id") or 0),
                "tool": tool_name,
                "args": payload.get("args"),
            })
        elif event_type == "tool_result":
            if str(payload.get("run_id") or "") != run_id:
                raise ExecutionLost("已提交工具结果与 Run 身份不一致，需要人工核对")
            result_events.append({
                "event_id": int(event.get("event_id") or 0),
                "task_id": task_id,
                "tool": tool_name,
                "result": payload.get("result"),
            })

    outcomes = [dict(item) for item in values.get("tool_outcomes", []) if isinstance(item, dict)]
    known_call_ids = {str(item.get("call_id") or "") for item in outcomes}
    evidence_records = [dict(item) for item in values.get("evidence_records", []) if isinstance(item, dict)]
    known_evidence_ids = {str(item.get("evidence_id") or "") for item in evidence_records}
    claims = [dict(item) for item in values.get("claims", []) if isinstance(item, dict)]
    known_claim_ids = {str(item.get("claim_id") or "") for item in claims}

    for event in result_events:
        task_id = event["task_id"]
        task_data = tasks_by_id.get(task_id)
        if task_data is None:
            raise ExecutionLost("已提交工具结果不属于检查点任务计划，需要人工核对")
        if str(task_data.get("tool_name") or "") != event["tool"]:
            raise ExecutionLost("已提交工具结果与任务工具不一致，需要人工核对")

        candidates = calls_by_task.get(task_id, [])
        preceding = [call for call in candidates if call["event_id"] < event["event_id"]]
        if not preceding:
            raise ExecutionLost("已提交工具结果缺少对应的工具调用，需要人工核对")
        call = preceding[-1]
        candidates.remove(call)
        if call["tool"] != event["tool"] or call["args"] != task_data.get("arguments", {}):
            raise ExecutionLost("已提交工具调用与冻结任务参数不一致，需要人工核对")

        try:
            outcome = ToolOutcome.model_validate(event["result"])
        except Exception as exc:
            raise ExecutionLost("已提交工具结果无法按当前契约恢复，需要人工核对") from exc
        if outcome.tool_name != event["tool"]:
            raise ExecutionLost("已提交工具结果与工具调用不一致，需要人工核对")
        if outcome.call_id in known_call_ids:
            continue
        if task_data.get("status") not in {"pending", "running"}:
            raise ExecutionLost("已提交工具结果与任务完成状态不一致，需要人工核对")

        task = HarnessTask.model_validate(task_data)
        task.status = "completed" if outcome.status in {"success", "partial", "not_found"} else "failed"
        task.attempts += 1
        task_data.update(task.model_dump(mode="json"))
        outcome_data = outcome.model_dump(mode="json")
        outcomes.append(outcome_data)
        known_call_ids.add(outcome.call_id)

        for evidence in _payload_evidence(outcome, task):
            item = evidence.model_dump(mode="json")
            if item["evidence_id"] not in known_evidence_ids:
                evidence_records.append(item)
                known_evidence_ids.add(item["evidence_id"])
        for claim in _payload_claims(outcome, task):
            item = claim.model_dump(mode="json")
            if item["claim_id"] not in known_claim_ids:
                claims.append(item)
                known_claim_ids.add(item["claim_id"])

    if not result_events:
        return values
    return {
        **values,
        "task_specs": task_specs,
        "tool_outcomes": outcomes,
        "evidence_records": evidence_records,
        "claims": claims,
    }
