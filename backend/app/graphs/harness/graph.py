"""The single, bounded LangGraph runtime for Agent work.

The graph deliberately accepts a resolved execution context and an explicit
task matrix. It does not read MongoDB, re-route intents, or call an LLM inside
nodes. Those responsibilities belong to the API/adapter and the future
planner integration; this makes the runtime deterministic and testable.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graphs.agent_core.answer_contract import AgentAnswer, build_agent_answer
from app.graphs.agent_core.evidence_ledger import (
    Claim,
    EvidenceLedger,
    EvidenceRecord,
    EvidenceStatus,
    build_evidence_record,
)
from app.tools import TOOL_REGISTRY
from app.tools.executor import ToolContext, ToolExecutor, ToolOutcome
from app.graphs.harness.state import (
    ExecutionBudget,
    HarnessState,
    HarnessTask,
    new_budget,
    utc_now_iso,
)
from app.domains.risk.risk_contract import get_risk_dimension_spec


PersistCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]

_DIMENSION_TO_TOOL = {
    dimension: (spec.tool_name, spec.argument_name)
    for dimension in (
        "risk", "financial", "business_risk", "quality", "delivery",
        "esg", "sentiment", "compliance",
    )
    if (spec := get_risk_dimension_spec(dimension)) is not None
}


def _entity_id(name: str, context: Mapping[str, Any]) -> str:
    for reference in context.get("references", []):
        if not isinstance(reference, dict) or reference.get("name") != name:
            continue
        return str(
            reference.get("supplier_id")
            or reference.get("company_id")
            or reference.get("candidate_id")
            or f"entity:{name}"
        )
    return f"entity:{name}"


def _task_from_subtask(
    task: Mapping[str, Any],
    subtask: Mapping[str, Any],
    context: Mapping[str, Any],
) -> HarnessTask | None:
    dimension = str(subtask.get("dimension") or "").strip()
    supplier_name = str(subtask.get("supplier_name") or "").strip()
    if dimension == "sourcing":
        requirement = task.get("requirement") or {}
        request_id = str(requirement.get("request_id") or "").strip() if isinstance(requirement, dict) else ""
        if request_id:
            return HarnessTask(
                task_id=str(subtask.get("subtask_id") or "sourcing"),
                tool_name="search_suppliers",
                arguments={"request_id": request_id},
                entity_id="sourcing",
                dimension="sourcing",
                required=bool(subtask.get("required", True)),
                evidence_requirements=["supplier_candidate"],
            )
        if isinstance(requirement, dict) and requirement.get("category"):
            return HarnessTask(
                task_id=str(subtask.get("subtask_id") or "sourcing"),
                tool_name="discover_supplier_candidates",
                arguments={"requirement": requirement},
                entity_id="sourcing",
                dimension="sourcing",
                required=bool(subtask.get("required", True)),
                evidence_requirements=["supplier_candidate"],
            )
        if not task.get("user_message") or _is_formal_directory_query(task):
            return HarnessTask(
                task_id=str(subtask.get("subtask_id") or "sourcing"),
                tool_name="list_formal_suppliers",
                arguments={"limit": 20},
                entity_id="sourcing",
                dimension="sourcing",
                required=bool(subtask.get("required", True)),
                evidence_requirements=["supplier_candidate"],
            )
        return None
    mapping = _DIMENSION_TO_TOOL.get(dimension)
    if not mapping or not supplier_name:
        return None
    tool_name, argument_name = mapping
    arguments = {argument_name: supplier_name}
    if tool_name == "assess_operational_risk":
        arguments["dimension"] = dimension
    return HarnessTask(
        task_id=str(subtask.get("subtask_id") or f"{task.get('task_id', 'task')}-{dimension}-{supplier_name}"),
        tool_name=tool_name,
        arguments=arguments,
        entity_id=_entity_id(supplier_name, context),
        dimension=dimension,
        required=bool(subtask.get("required", True)),
        evidence_requirements=[str(item) for item in subtask.get("evidence_requirements", [dimension])],
    )


def _build_default_plan(state: HarnessState) -> list[HarnessTask]:
    current_task = state.get("current_task") or {}
    context = state.get("execution_context") or {}
    explicit = state.get("task_specs") or []
    if explicit:
        return [HarnessTask.model_validate(item) for item in explicit]

    subtasks = current_task.get("subtasks", [])
    planned = [
        result
        for item in subtasks
        if isinstance(item, dict)
        for result in [_task_from_subtask(current_task, item, context)]
        if result is not None
    ]
    if planned:
        return planned

    names = [str(item).strip() for item in current_task.get("target_supplier_names", []) if str(item).strip()]
    dimensions = [str(item).strip() for item in current_task.get("analysis_dimensions", []) if str(item).strip()]
    result: list[HarnessTask] = []
    if current_task.get("task_type") == "sourcing":
        requirement = current_task.get("requirement") or {}
        request_id = str(requirement.get("request_id") or "").strip() if isinstance(requirement, dict) else ""
        if isinstance(requirement, dict) and requirement.get("category") and not request_id:
            result.append(
                HarnessTask(
                    task_id=f"{current_task.get('task_id', 'task')}:sourcing",
                    tool_name="discover_supplier_candidates",
                    arguments={"requirement": requirement},
                    entity_id="sourcing",
                    dimension="sourcing",
                    required=True,
                    evidence_requirements=["supplier_candidate"],
                )
            )
        elif _is_formal_directory_query(current_task):
            result.append(
                HarnessTask(
                    task_id=f"{current_task.get('task_id', 'task')}:sourcing",
                    tool_name="search_suppliers" if request_id else "list_formal_suppliers",
                    arguments={"request_id": request_id} if request_id else {"limit": 20},
                    entity_id="sourcing",
                    dimension="sourcing",
                    required=True,
                    evidence_requirements=["supplier_candidate"],
                )
            )
    for name in dict.fromkeys(names):
        for dimension in dict.fromkeys(dimensions):
            mapping = _DIMENSION_TO_TOOL.get(dimension)
            if not mapping:
                continue
            tool_name, argument_name = mapping
            arguments = {argument_name: name}
            if tool_name == "assess_operational_risk":
                arguments["dimension"] = dimension
            result.append(
                HarnessTask(
                    task_id=f"{current_task.get('task_id', 'task')}:{name}:{dimension}",
                    tool_name=tool_name,
                    arguments=arguments,
                    entity_id=_entity_id(name, context),
                    dimension=dimension,
                    required=dimension != "sentiment",
                    evidence_requirements=[dimension],
                )
            )
    return result


def _is_formal_directory_query(task: Mapping[str, Any]) -> bool:
    """Only a directory question may use the unfiltered formal-supplier tool."""
    message = str(task.get("user_message") or "")
    return "正式供应商" in message and any(token in message for token in ("哪些", "列表", "目录", "清单"))


def _payload_evidence(
    outcome: ToolOutcome,
    task: HarnessTask,
) -> list[EvidenceRecord]:
    raw_records = outcome.data.get("evidence_records") or outcome.data.get("evidence") or []
    if not isinstance(raw_records, list):
        return []
    records: list[EvidenceRecord] = []
    for index, raw in enumerate(raw_records):
        if isinstance(raw, EvidenceRecord):
            records.append(raw)
            continue
        if not isinstance(raw, dict):
            continue
        try:
            records.append(EvidenceRecord.model_validate(raw))
            continue
        except Exception:
            pass
        payload = dict(raw)
        status_value = payload.pop("status", EvidenceStatus.AVAILABLE)
        try:
            status = EvidenceStatus(str(status_value))
        except ValueError:
            status = EvidenceStatus.UNAVAILABLE
        data_mode = str(payload.pop("data_mode", "formal"))
        if data_mode not in {"formal", "synthetic"}:
            data_mode = "formal"
        endpoint = payload.pop("endpoint", None)
        records.append(
            build_evidence_record(
                evidence_id=str(payload.pop("evidence_id", f"{outcome.call_id}:evidence:{index}")),
                entity_id=str(payload.pop("entity_id", task.entity_id)),
                dimension=str(payload.pop("dimension", task.dimension)),
                provider=str(payload.pop("provider", outcome.tool_name)),
                source_type=str(payload.pop("source_type", "unknown")),
                payload=payload,
                status=status,
                data_mode=data_mode,
                endpoint=endpoint,
            )
        )
    return records


def _payload_claims(outcome: ToolOutcome, task: HarnessTask) -> list[Claim]:
    raw_claims = outcome.data.get("claims") or []
    if not isinstance(raw_claims, list):
        return []
    claims: list[Claim] = []
    for index, raw in enumerate(raw_claims):
        if not isinstance(raw, dict):
            continue
        try:
            claims.append(
                Claim(
                    claim_id=str(raw.get("claim_id") or f"{outcome.call_id}:claim:{index}"),
                    entity_id=str(raw.get("entity_id") or task.entity_id),
                    dimension=str(raw.get("dimension") or task.dimension),
                    statement=str(raw["statement"]),
                    value=raw.get("value"),
                    fact_path=str(raw["fact_path"]) if raw.get("fact_path") else None,
                    operator=str(raw.get("operator") or "eq"),
                    unit=str(raw["unit"]) if raw.get("unit") else None,
                    evidence_refs=[str(item) for item in raw.get("evidence_refs", [])],
                    confidence=float(raw.get("confidence", 0.0)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return claims


def _required_dimensions(tasks: list[HarnessTask]) -> list[str]:
    return list(dict.fromkeys(task.dimension for task in tasks if task.required))


def _required_evidence_items(tasks: list[HarnessTask]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for task in tasks:
        if not task.required:
            continue
        requirements = task.evidence_requirements or [task.dimension]
        for requirement in requirements:
            items.append({
                "entity_id": task.entity_id,
                "dimension": task.dimension,
                "fact_path": (
                    requirement
                    if requirement not in {task.dimension, "supplier_candidate"}
                    else ""
                ),
            })
    return items


def _summary(answer: AgentAnswer, state: HarnessState) -> str:
    if answer.status == "completed":
        return "已完成基于有效证据的 Agent 分析。"
    if answer.status == "partial":
        return "已完成部分分析，部分结果来自演示数据或覆盖不足。"
    if answer.status == "needs_review":
        return "分析已停止在证据复核点，当前不输出未经证据支持的确定性结论。"
    return "Agent 运行未完成。"


def build_harness_graph(
    *,
    executor: ToolExecutor | None = None,
    persist: PersistCallback | None = None,
    checkpointer: Any = None,
) -> Any:
    """Build the only runtime graph used by Harness-level tests and callers."""

    active_executor = executor or ToolExecutor(TOOL_REGISTRY)

    async def persist_event(event: str, state: HarnessState, patch: dict[str, Any] | None = None) -> None:
        snapshot = dict(state)
        if patch:
            snapshot.update(patch)
        events = list(snapshot.get("events", []))
        events.append({"event": event, "at": utc_now_iso()})
        snapshot["events"] = events
        if persist:
            result = persist(event, snapshot)
            if inspect.isawaitable(result):
                await result

    async def load_session(state: HarnessState) -> dict[str, Any]:
        required = ("session_id", "turn_id", "run_id", "user_message")
        missing = [key for key in required if not str(state.get(key) or "").strip()]
        if missing:
            raise ValueError(f"Harness 身份字段缺失: {', '.join(missing)}")
        budget = new_budget(state.get("budget"))
        patch = {
            "schema_version": 1,
            "budget": budget.model_dump(mode="json"),
            "tool_call_count": int(state.get("tool_call_count", 0)),
            "llm_call_count": int(state.get("llm_call_count", 0)),
            "loop_iterations": int(state.get("loop_iterations", 0)),
            "remediation_attempts": int(state.get("remediation_attempts", 0)),
            "status": "loaded",
            "started_at": state.get("started_at") or utc_now_iso(),
            "tool_outcomes": list(state.get("tool_outcomes", [])),
            "evidence_records": list(state.get("evidence_records", [])),
            "claims": list(state.get("claims", [])),
        }
        await persist_event("load_session", state, patch)
        return patch

    async def resolve_turn(state: HarnessState) -> dict[str, Any]:
        context = state.get("execution_context")
        if not isinstance(context, dict):
            raise ValueError("Harness execution_context 必须是单一已解析快照")
        current_task = state.get("current_task") or context.get("current_task") or {}
        if not isinstance(current_task, dict):
            raise ValueError("Harness current_task 契约无效")
        patch = {"current_task": dict(current_task), "status": "turn_resolved"}
        await persist_event("resolve_turn", state, patch)
        return patch

    async def build_plan(state: HarnessState) -> dict[str, Any]:
        tasks = _build_default_plan(state)
        patch = {"task_specs": [task.model_dump(mode="json") for task in tasks], "status": "planned"}
        await persist_event("build_plan", state, patch)
        return patch

    async def execute_ready_tasks(state: HarnessState) -> dict[str, Any]:
        budget = new_budget(state.get("budget"))
        tasks = [HarnessTask.model_validate(item) for item in state.get("task_specs", [])]
        outcomes = list(state.get("tool_outcomes", []))
        evidence_records = list(state.get("evidence_records", []))
        claims = list(state.get("claims", []))
        count = int(state.get("tool_call_count", 0))
        started = _parse_time(state.get("started_at"))
        executed = 0
        for task in tasks:
            if task.status == "completed":
                continue
            if count >= budget.max_tool_calls:
                break
            if (datetime.now(timezone.utc) - started).total_seconds() >= budget.max_duration_seconds:
                break
            context = ToolContext(
                session_id=state["session_id"],
                run_id=state["run_id"],
                user_id=state.get("user_id"),
                tool_call_count=count,
                max_tool_calls=budget.max_tool_calls,
            )
            outcome = await active_executor.execute(task.tool_name, task.arguments, context)
            count += 1
            executed += 1
            task.status = "completed" if outcome.status in {"success", "partial", "not_found"} else "failed"
            task.attempts += 1
            outcomes.append(outcome.model_dump(mode="json"))
            for record in _payload_evidence(outcome, task):
                evidence_records.append(record.model_dump(mode="json"))
            claims.extend(claim.model_dump(mode="json") for claim in _payload_claims(outcome, task))
        status = "executed" if executed else "budget_exhausted"
        patch = {
            "task_specs": [task.model_dump(mode="json") for task in tasks],
            "tool_outcomes": outcomes,
            "evidence_records": evidence_records,
            "claims": claims,
            "tool_call_count": count,
            "status": status,
        }
        await persist_event("execute_ready_tasks", state, patch)
        return patch

    async def validate_evidence(state: HarnessState) -> dict[str, Any]:
        ledger = EvidenceLedger(
            [EvidenceRecord.model_validate(item) for item in state.get("evidence_records", [])]
        )
        claims = [Claim.model_validate(item) for item in state.get("claims", [])]
        tasks = [HarnessTask.model_validate(item) for item in state.get("task_specs", [])]
        required = _required_dimensions(tasks)
        coverage = ledger.coverage(required, _required_evidence_items(tasks))
        validated = [ledger.validate_claim(claim).claim.model_dump(mode="json") for claim in claims]
        missing = list(coverage.missing_dimensions)
        patch = {
            "validated_claims": validated,
            "evidence_coverage": coverage.model_dump(mode="json"),
            "status": "evidence_validated" if not missing else "evidence_incomplete",
        }
        await persist_event("validate_evidence", state, patch)
        return patch

    def route_after_validation(state: HarnessState) -> str:
        coverage = state.get("evidence_coverage") or {}
        missing = coverage.get("missing_dimensions", [])
        attempts = int(state.get("remediation_attempts", 0))
        budget = new_budget(state.get("budget"))
        if missing and state.get("remediation_specs") and attempts < budget.max_loop_iterations:
            return "remediate"
        return "render_answer"

    async def remediate(state: HarnessState) -> dict[str, Any]:
        attempts = int(state.get("remediation_attempts", 0)) + 1
        existing = [item for item in state.get("task_specs", []) if isinstance(item, dict)]
        existing_ids = {str(item.get("task_id")) for item in existing}
        existing_sources = {
            str(item.get("source_key")) for item in existing if item.get("source_key")
        }
        additions = [
            item for item in state.get("remediation_specs", [])
            if isinstance(item, dict)
            and str(item.get("task_id")) not in existing_ids
            and (
                not item.get("source_key")
                or str(item.get("source_key")) not in existing_sources
            )
        ]
        patch = {
            "task_specs": list(state.get("task_specs", [])) + additions,
            "remediation_attempts": attempts,
            "loop_iterations": int(state.get("loop_iterations", 0)) + 1,
            "status": "remediating",
        }
        await persist_event("remediate", state, patch)
        return patch

    async def render_answer(state: HarnessState) -> dict[str, Any]:
        ledger = EvidenceLedger(
            [EvidenceRecord.model_validate(item) for item in state.get("evidence_records", [])]
        )
        claims = [Claim.model_validate(item) for item in state.get("claims", [])]
        tasks = [HarnessTask.model_validate(item) for item in state.get("task_specs", [])]
        answer = build_agent_answer(
            summary="待生成",
            ledger=ledger,
            claims=claims,
            required_dimensions=_required_dimensions(tasks),
            required_evidence=_required_evidence_items(tasks),
        )
        if not claims:
            answer = answer.model_copy(
                update={
                    "status": "needs_review",
                    "summary": "未形成任何可由证据支持的确定性结论。",
                    "limitations": list(dict.fromkeys([*answer.limitations, "工具结果未提供可验证 Claim"])),
                }
            )
        else:
            answer = answer.model_copy(update={"summary": _summary(answer, state)})
        patch = {"answer": answer.model_dump(mode="json"), "status": answer.status}
        await persist_event("render_answer", state, patch)
        return patch

    async def persist_turn(state: HarnessState) -> dict[str, Any]:
        await persist_event("persist_turn", state)
        return {"status": state.get("status", "needs_review")}

    graph = StateGraph(HarnessState)
    graph.add_node("load_session", load_session)
    graph.add_node("resolve_turn", resolve_turn)
    graph.add_node("build_plan", build_plan)
    graph.add_node("execute_ready_tasks", execute_ready_tasks)
    graph.add_node("validate_evidence", validate_evidence)
    graph.add_node("remediate", remediate)
    graph.add_node("render_answer", render_answer)
    graph.add_node("persist_turn", persist_turn)
    graph.add_edge(START, "load_session")
    graph.add_edge("load_session", "resolve_turn")
    graph.add_edge("resolve_turn", "build_plan")
    graph.add_edge("build_plan", "execute_ready_tasks")
    graph.add_edge("execute_ready_tasks", "validate_evidence")
    graph.add_conditional_edges(
        "validate_evidence",
        route_after_validation,
        {"remediate": "remediate", "render_answer": "render_answer"},
    )
    graph.add_edge("remediate", "execute_ready_tasks")
    graph.add_edge("render_answer", "persist_turn")
    graph.add_edge("persist_turn", END)
    return graph.compile(checkpointer=checkpointer) if checkpointer is not None else graph.compile()


async def run_harness(
    state: HarnessState,
    *,
    executor: ToolExecutor | None = None,
    persist: PersistCallback | None = None,
    checkpointer: Any = None,
    config: dict[str, Any] | None = None,
) -> HarnessState:
    """Invoke the unified graph with the required LangGraph thread identity."""
    active_config = dict(config or {})
    configurable = dict(active_config.get("configurable") or {})
    configurable.setdefault("thread_id", state["run_id"])
    active_config["configurable"] = configurable
    graph = build_harness_graph(executor=executor, persist=persist, checkpointer=checkpointer)
    return await graph.ainvoke(state, config=active_config)


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


__all__ = ["build_harness_graph", "run_harness"]
