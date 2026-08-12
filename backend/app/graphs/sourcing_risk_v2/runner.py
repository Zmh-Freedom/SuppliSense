"""Construction and durable start/resume entry points for Sourcing Risk V2."""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.domains.agent_run.service import get_orchestration_run
from app.graphs.sourcing_risk_v2 import nodes
from app.graphs.sourcing_risk_v2.checkpointer import compile_sourcing_risk_graph, get_sourcing_risk_checkpointer
from app.graphs.sourcing_risk_v2.state import SourcingRiskGraphState


def build_sourcing_risk_graph(checkpointer: Any = None) -> Any:
    """Compile V2 only through Task 4's persistent-checkpointer boundary."""
    graph = StateGraph(SourcingRiskGraphState)
    graph.add_node("load_run", nodes.load_run)
    graph.add_node("parse_requirement", nodes.parse_requirement_node)
    graph.add_node("lock_policy", nodes.lock_policy)
    graph.add_node("local_discovery", nodes.local_discovery)
    graph.add_node("external_discovery", nodes.external_discovery)
    graph.add_node("identity_resolution", nodes.identity_resolution)
    graph.add_node("identity_review", nodes.identity_review)
    graph.add_node("investigate_parallel", nodes.investigate_parallel)
    graph.add_node("validate_evidence", nodes.validate_evidence)
    graph.add_node("score_candidates", nodes.score_candidates)
    graph.add_node("ready_for_review", nodes.ready_for_review)
    graph.add_edge(START, "load_run")
    graph.add_edge("load_run", "parse_requirement")
    graph.add_conditional_edges("parse_requirement", nodes.route_after_requirement, {"end": END, "lock_policy": "lock_policy"})
    graph.add_edge("lock_policy", "local_discovery")
    graph.add_conditional_edges("local_discovery", nodes.route_after_discovery, {"external_discovery": "external_discovery", "identity_resolution": "identity_resolution"})
    graph.add_edge("external_discovery", "identity_resolution")
    graph.add_conditional_edges(
        "identity_resolution",
        lambda state: "identity_review" if state.get("status") == "IDENTITY_REVIEW" else "investigate_parallel",
        {"identity_review": "identity_review", "investigate_parallel": "investigate_parallel"},
    )
    graph.add_edge("identity_review", "investigate_parallel")
    graph.add_edge("investigate_parallel", "validate_evidence")
    graph.add_edge("validate_evidence", "score_candidates")
    graph.add_edge("score_candidates", "ready_for_review")
    graph.add_edge("ready_for_review", END)
    return compile_sourcing_risk_graph(graph, checkpointer=checkpointer)


async def start_sourcing_risk_graph(run_id: str) -> None:
    """Start a run from its persisted requirement under the persistent checkpointer."""
    await _start(run_id)


async def resume_sourcing_risk_graph(run_id: str, resume_payload: dict[str, Any]) -> None:
    """Resume a checkpointed reviewer pause without any process-local storage."""
    await _resume(run_id, resume_payload)


async def _start(run_id: str) -> None:
    run = await asyncio.to_thread(get_orchestration_run, run_id)
    if run is None:
        raise ValueError("agent run 不存在")
    checkpointer = await get_sourcing_risk_checkpointer()
    graph = build_sourcing_risk_graph(checkpointer)
    await graph.ainvoke({"run_id": run_id, "requirement_input": dict(run.get("requirement") or {})}, _config(run_id))


async def _resume(run_id: str, resume_payload: dict[str, Any]) -> None:
    checkpointer = await get_sourcing_risk_checkpointer()
    graph = build_sourcing_risk_graph(checkpointer)
    await graph.ainvoke(Command(resume=resume_payload), _config(run_id))


def _config(run_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": run_id}}
