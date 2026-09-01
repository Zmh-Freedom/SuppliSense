"""Durable orchestration graph for composite sourcing-risk tasks."""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.core.config import settings
from app.core.rollout_gate import require_v2_execution
from app.domains.agent_run import service as agent_run_service
from app.domains.agent_run.service import get_orchestration_run
from app.graphs.agent_core.contracts import LoopState
from app.graphs.agent_core.validator import validate_evidence
from app.graphs.agent_supervisor.agents import run_agent_task, run_ready_tasks
from app.graphs.agent_supervisor.contracts import AgentResult, TaskPlan
from app.graphs.agent_supervisor.decision import build_decision
from app.graphs.agent_supervisor.evidence import merge_evidence
from app.graphs.agent_supervisor.planner import fallback_requirement_from_query, plan_agent_task
from app.graphs.agent_supervisor.state import AgentTaskState
from app.graphs.approval import request_supervisor_approval
from app.graphs.sourcing_risk_v2.checkpointer import (
    compile_sourcing_risk_graph,
    get_sourcing_risk_checkpointer,
)


async def _call_sync(function: Any, *args: Any) -> Any:
    result = await asyncio.to_thread(function, *args)
    return await result if inspect.isawaitable(result) else result


async def _persist(
    state: AgentTaskState,
    task_status: str,
    event_type: str,
    snapshot: dict[str, Any],
) -> None:
    await _call_sync(
        agent_run_service.persist_supervisor_snapshot,
        state["run_id"],
        task_status,
        event_type,
        snapshot,
    )


def _task_plan(state: AgentTaskState) -> TaskPlan:
    return TaskPlan.model_validate(state.get("plan", {}))


def _agent_results(state: AgentTaskState) -> dict[str, AgentResult]:
    return {
        task_id: (
            result
            if isinstance(result, AgentResult)
            else AgentResult.model_validate(result)
        )
        for task_id, result in state.get("agent_results", {}).items()
    }


def _format_final_answer(
    decision_summary: str,
    results: dict[str, AgentResult],
    pending_approvals: list[dict[str, Any]],
) -> str:
    """Render evidence-backed worker outcomes without delegating facts to an LLM."""
    labels = {
        "risk": "风险",
        "esg": "ESG",
        "sentiment": "舆情",
        "compliance": "合规",
        "sourcing": "寻源",
    }
    lines = [decision_summary]
    for task_id, result in results.items():
        label = labels.get(result.agent, task_id)
        if result.evidence:
            claims = "；".join(
                item.claim for item in result.evidence if item.claim
            )
            lines.append(f"{label}：{claims or result.summary}")
        else:
            lines.append(f"{label}：{result.summary}")
    if pending_approvals:
        lines.append(
            f"已生成 {len(pending_approvals)} 项加入监控操作，等待人工确认后才会写入监控清单。"
        )
    return "\n\n".join(lines)


def _ready_task_ids(
    plan: TaskPlan, results: dict[str, AgentResult]
) -> list[str]:
    return [
        task.task_id
        for task in plan.tasks
        if task.task_id not in results
        and all(
            dependency in results and results[dependency].status == "completed"
            for dependency in task.depends_on
        )
    ]


async def load_task(state: AgentTaskState) -> dict[str, Any]:
    """Initialize a checkpointable task without loading business data."""
    if not state.get("run_id"):
        raise ValueError("run_id 不能为空")
    await _persist(state, "CREATED", "stage", {"stage": "load_task"})
    return {
        "task_status": "CREATED",
        "agent_results": dict(state.get("agent_results", {})),
        "pending_approvals": list(state.get("pending_approvals", [])),
        "error": None,
    }


async def plan_task(state: AgentTaskState) -> dict[str, Any]:
    """Build and durably expose the deterministic task plan."""
    persisted_plan = state.get("plan")
    if isinstance(persisted_plan, dict) and persisted_plan.get("tasks"):
        return {"plan": persisted_plan, "task_status": "PLANNING"}

    intent = dict(state.get("intent", {}))
    references = state.get("supplier_references", [])
    current_task = intent.get("current_task")
    if isinstance(current_task, dict):
        target_names = current_task.get("target_supplier_names", [])
        dimensions = current_task.get("analysis_dimensions", [])
        if target_names:
            intent["company_name"] = target_names[0]
            intent["target_supplier_names"] = list(target_names)
        if dimensions:
            intent["analysis_dimensions"] = list(dimensions)
    if (
        "company_name" not in intent
        and references
        and any(
            token in state.get("user_query", "")
            for token in ("它", "这家", "该供应商", "该企业")
        )
    ):
        first_reference = references[0]
        if isinstance(first_reference, dict) and first_reference.get("name"):
            intent["company_name"] = first_reference["name"]
            intent["target_supplier_names"] = [first_reference["name"]]
    if not intent.get("target_supplier_names"):
        reference_names = [
            reference.get("name")
            for reference in references
            if isinstance(reference, dict) and isinstance(reference.get("name"), str)
        ]
        if reference_names:
            intent["target_supplier_names"] = list(dict.fromkeys(reference_names))
    intent["request_watchlist"] = "监控" in state.get("user_query", "")
    if "requirement" not in intent:
        from app.domains.sourcing_risk.requirement_service import parse_requirement

        parsed = await _call_sync(parse_requirement, state.get("user_query", ""))
        if parsed.get("status") == "ready":
            intent["requirement"] = parsed["requirement"]
        else:
            fallback = fallback_requirement_from_query(state.get("user_query", ""))
            if fallback:
                intent["requirement"] = fallback
    plan = plan_agent_task(state.get("user_query", ""), intent)
    serialized = plan.model_dump(mode="json")
    current_task = intent.get("current_task")
    current_task = current_task if isinstance(current_task, dict) else {}
    target_supplier_names = current_task.get("target_supplier_names")
    analysis_dimensions = current_task.get("analysis_dimensions")
    analysis_scope = {
        "target_supplier_names": (
            list(target_supplier_names)
            if isinstance(target_supplier_names, list)
            else [intent["company_name"]] if isinstance(intent.get("company_name"), str) else []
        ),
        "analysis_dimensions": (
            list(analysis_dimensions) if isinstance(analysis_dimensions, list) else []
        ),
    }
    await _persist(
        state,
        "PLANNING",
        "planning",
        {"plan": serialized, "analysis_scope": analysis_scope},
    )
    return {"plan": serialized, "intent": intent, "task_status": "PLANNING"}


async def execute_ready_tasks(state: AgentTaskState) -> dict[str, Any]:
    """Execute dependency waves until no additional task is ready."""
    plan = _task_plan(state)
    results = _agent_results(state)

    while ready_ids := _ready_task_ids(plan, results):
        ready_tasks = [plan.task(task_id) for task_id in ready_ids]
        for task in ready_tasks:
            await _persist(
                state,
                "EXECUTING",
                "agent_start",
                {"task_id": task.task_id, "agent": task.agent},
            )

        current_state: AgentTaskState = {
            **state,
            "agent_results": {
                task_id: result.model_dump(mode="json")
                for task_id, result in results.items()
            },
        }
        completed_wave = await run_ready_tasks(plan, current_state)
        if not completed_wave:
            break
        for task_id, result in completed_wave.items():
            results[task_id] = result
            await _persist(
                state,
                "EXECUTING",
                "agent_result",
                {"task_id": task_id, "result": result.model_dump(mode="json")},
            )

    findings = [
        finding.model_dump(mode="json")
        for result in results.values()
        for finding in result.findings
    ]
    recommendations = [
        action.model_dump(mode="json")
        for result in results.values()
        for action in result.recommended_actions
    ]
    intent = state.get("intent", {})
    target_names = intent.get("target_supplier_names", []) if isinstance(intent, dict) else []
    if isinstance(intent, dict) and intent.get("request_watchlist") and isinstance(target_names, list):
        recommendations.extend({
            "action_type": "add_watchlist",
            "target": {
                "company_name": company_name,
                "target_source": "conversation_state",
            },
            "reason": "用户要求对该供应商持续进行风险监控。",
            "impact": "加入本地风险监控清单，后续定时检查风险与舆情变化。",
            "requires_approval": True,
        } for company_name in target_names if isinstance(company_name, str) and company_name.strip())
    return {
        "agent_results": {
            task_id: result.model_dump(mode="json")
            for task_id, result in results.items()
        },
        "findings": findings,
        "recommendations": recommendations,
        "task_status": "EXECUTING",
    }


async def merge_task_evidence(state: AgentTaskState) -> dict[str, Any]:
    """Merge evidence and evaluate whether one targeted completion is justified."""
    results = _agent_results(state)
    plan = _task_plan(state)
    merged = merge_evidence(results, plan)
    serialized = merged.model_dump(mode="json")
    remediation_attempts = int(state.get("evidence_remediation_attempts", 0))
    raw_loop = state.get("evidence_loop")
    loop_state = (
        LoopState.model_validate(raw_loop)
        if isinstance(raw_loop, dict)
        else LoopState(
            loop_type="evidence",
            iteration=0,
            max_iterations=2,
            tool_call_count=0,
            max_tool_calls=2,
            started_at=datetime.now(timezone.utc),
            timeout_seconds=60,
        )
    )
    validation = validate_evidence(
        merged,
        plan,
        results,
        loop_state,
        remediation_attempts=remediation_attempts,
    )
    validation_serialized = validation.model_dump(mode="json")
    await _persist(
        state,
        "EVIDENCE_MERGING",
        "evidence_merge",
        {
            "evidence_merge": serialized,
            "evidence_validation": validation_serialized,
        },
    )
    return {
        "evidence": serialized["evidence"],
        "evidence_validation": validation_serialized,
        "evidence_loop": loop_state.model_dump(mode="json"),
        "task_status": "EVIDENCE_MERGING",
    }


async def remediate_task_evidence(state: AgentTaskState) -> dict[str, Any]:
    """Retry only missing required dimensions once, under the evidence budget."""
    validation = state.get("evidence_validation", {})
    if not isinstance(validation, dict) or not validation.get("should_remediate"):
        return {}
    attempts = int(state.get("evidence_remediation_attempts", 0))
    if attempts >= 1:
        return {}

    plan = _task_plan(state)
    results = _agent_results(state)
    missing_dimensions = set(validation.get("insufficient_dimensions", []))
    remediation_tasks = [
        task
        for task in plan.tasks
        if task.required and task.agent in missing_dimensions
    ][:2]
    if not remediation_tasks:
        return {}

    for task in remediation_tasks:
        result = await run_agent_task(task, {
            **state,
            "agent_results": {
                task_id: item.model_dump(mode="json")
                for task_id, item in results.items()
            },
        })
        results[task.task_id] = result
        await _persist(
            state,
            "EXECUTING",
            "evidence_remediation_result",
            {"task_id": task.task_id, "result": result.model_dump(mode="json")},
        )

    raw_loop = state.get("evidence_loop", {})
    loop_state = LoopState.model_validate(raw_loop)
    next_loop = loop_state.model_copy(update={
        "iteration": loop_state.iteration + 1,
        "tool_call_count": loop_state.tool_call_count + len(remediation_tasks),
        "evidence_count_before": len(state.get("evidence", [])),
    })
    return {
        "agent_results": {
            task_id: result.model_dump(mode="json")
            for task_id, result in results.items()
        },
        "evidence_remediation_attempts": attempts + 1,
        "evidence_loop": next_loop.model_dump(mode="json"),
        "task_status": "EXECUTING",
    }


def _after_evidence_merge(state: AgentTaskState) -> str:
    validation = state.get("evidence_validation", {})
    if isinstance(validation, dict) and validation.get("should_remediate"):
        return "remediate_evidence"
    return "build_decision"


async def build_task_decision(state: AgentTaskState) -> dict[str, Any]:
    """Build a pure decision and expose approvals before the interrupt node."""
    merged = merge_evidence(_agent_results(state), _task_plan(state))
    decision = build_decision(state, merged)
    serialized = decision.model_dump(mode="json")
    await _persist(
        state,
        "DECISION_READY",
        "decision_ready",
        {"decision": serialized},
    )
    pending_approvals = serialized["pending_approvals"]
    if pending_approvals:
        pending_approvals = await _call_sync(
            agent_run_service.create_supervisor_action_proposals,
            state["run_id"],
            pending_approvals,
        )
    final_answer = _format_final_answer(
        serialized["summary"], _agent_results(state), pending_approvals
    )
    task_status = (
        "WAITING_HUMAN_APPROVAL" if pending_approvals else "DECISION_READY"
    )
    if pending_approvals:
        await _persist(
            state,
            task_status,
            "approval_required",
            {"pending_approvals": pending_approvals},
        )
    return {
        "recommendations": serialized["recommendations"],
        "pending_approvals": pending_approvals,
        "final_answer": final_answer,
        "task_status": task_status,
    }


async def approval_gate(state: AgentTaskState) -> dict[str, Any]:
    """Interrupt before writes and enter the approved-only V2 boundary on resume."""
    approvals = [dict(approval) for approval in state.get("pending_approvals", [])]
    if not approvals:
        return {}

    decision = request_supervisor_approval(approvals)
    approval_status = str(decision["status"])
    if decision.get("approved") is True:
        await _call_sync(
            agent_run_service.approve_supervisor_action_proposals,
            state["run_id"],
            [approval["approval_id"] for approval in approvals],
        )
        for approval in approvals:
            await _call_sync(
                agent_run_service.execute_supervisor_approved_action,
                state["run_id"],
                approval["approval_id"],
            )

    decided = [{**approval, "status": approval_status} for approval in approvals]
    await _persist(
        state,
        "DECISION_READY",
        "approval",
        {
            "approved": decision.get("approved") is True,
            "status": approval_status,
            "reason": decision.get("reason"),
            "pending_approvals": decided,
        },
    )
    return {"pending_approvals": decided, "task_status": "DECISION_READY"}


async def finalize(state: AgentTaskState) -> dict[str, Any]:
    """Produce a terminal answer while retaining partial-result semantics."""
    plan = _task_plan(state)
    results = _agent_results(state)
    required_incomplete = any(
        task.required
        and (
            task.task_id not in results
            or results[task.task_id].status != "completed"
        )
        for task in plan.tasks
    )
    task_status = "PARTIAL_COMPLETED" if required_incomplete else "COMPLETED"
    approval_statuses = {
        str(approval.get("status"))
        for approval in state.get("pending_approvals", [])
    }
    suffix = (
        " 待执行操作已被拒绝或过期，未写入业务数据。"
        if approval_statuses.intersection({"rejected", "expired"})
        else ""
    )
    final_answer = f"{state.get('final_answer', '')}{suffix}".strip()
    await _persist(
        state,
        task_status,
        "done",
        {"final_answer": final_answer},
    )
    return {"task_status": task_status, "final_answer": final_answer}


def build_agent_supervisor_graph(
    checkpointer: Any = None,
) -> CompiledStateGraph:
    """Compile the Supervisor through the Sourcing Risk V2 checkpointer boundary."""
    graph = StateGraph(AgentTaskState)
    graph.add_node("load_task", load_task)
    graph.add_node("plan_task", plan_task)
    graph.add_node("execute_ready_tasks", execute_ready_tasks)
    graph.add_node("merge_evidence", merge_task_evidence)
    graph.add_node("remediate_evidence", remediate_task_evidence)
    graph.add_node("build_decision", build_task_decision)
    graph.add_node("approval_gate", approval_gate)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "load_task")
    graph.add_edge("load_task", "plan_task")
    graph.add_edge("plan_task", "execute_ready_tasks")
    graph.add_edge("execute_ready_tasks", "merge_evidence")
    graph.add_conditional_edges(
        "merge_evidence",
        _after_evidence_merge,
        {
            "remediate_evidence": "remediate_evidence",
            "build_decision": "build_decision",
        },
    )
    graph.add_edge("remediate_evidence", "merge_evidence")
    graph.add_edge("build_decision", "approval_gate")
    graph.add_edge("approval_gate", "finalize")
    graph.add_edge("finalize", END)
    return compile_sourcing_risk_graph(graph, checkpointer=checkpointer)


def _config(run_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": run_id}}


async def start_agent_supervisor(run_id: str) -> None:
    """Start or safely resume a Supervisor from durable execution state."""
    require_v2_execution(settings)
    run = await _call_sync(get_orchestration_run, run_id)
    if run is None:
        raise ValueError("agent run 不存在")
    requirement = dict(run.get("requirement") or {})
    intent = requirement.get("intent")
    if not isinstance(intent, dict):
        intent = {"requirement": dict(requirement)}
    user_query = str(
        requirement.get("user_query") or requirement.get("requirement_text") or ""
    )
    snapshot = await _call_sync(agent_run_service.load_execution_snapshot, run_id, run)
    graph_input: AgentTaskState = {
        "run_id": run_id,
        "user_query": user_query,
        "intent": dict(intent),
    }
    if snapshot:
        task_matrix = snapshot.get("task_matrix")
        if isinstance(task_matrix, list) and task_matrix:
            graph_input["plan"] = {"tasks": task_matrix}
        result_ledger = snapshot.get("subtask_results")
        if isinstance(result_ledger, dict):
            graph_input["agent_results"] = {
                task_id: result
                for task_id, result in result_ledger.items()
                if isinstance(result, dict) and result.get("status") == "completed"
            }
        loops = snapshot.get("loops")
        if isinstance(loops, dict) and isinstance(loops.get("evidence"), dict):
            graph_input["evidence_loop"] = dict(loops["evidence"])
        pending_approvals = snapshot.get("pending_approvals")
        if isinstance(pending_approvals, list) and pending_approvals:
            graph_input["pending_approvals"] = pending_approvals
    checkpointer = await get_sourcing_risk_checkpointer()
    graph = build_agent_supervisor_graph(checkpointer)
    await graph.ainvoke(
        graph_input,
        _config(run_id),
    )


async def resume_agent_supervisor(
    run_id: str, resume_payload: dict[str, Any]
) -> None:
    """Resume the durable approval interrupt for one Supervisor run."""
    require_v2_execution(settings)
    checkpointer = await get_sourcing_risk_checkpointer()
    graph = build_agent_supervisor_graph(checkpointer)
    await graph.ainvoke(Command(resume=dict(resume_payload)), _config(run_id))
