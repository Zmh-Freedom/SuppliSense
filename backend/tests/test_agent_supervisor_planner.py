"""Deterministic task-planning and supervisor-routing behavior."""

import pytest

from app.graphs.agent_supervisor.contracts import PlannerTask, TaskPlan
from app.graphs.agent_supervisor.planner import (
    is_composite_request,
    plan_agent_task,
    validate_task_plan,
)
from app.graphs.router import Intent, IntentRouter


def test_composite_request_plans_sourcing_and_parallel_risk_agents():
    """A composite sourcing request keeps analysis tasks dependent on sourcing."""
    plan = plan_agent_task("帮我找电机供应商并评估风险和合规")

    assert [task.agent for task in plan.tasks] == ["sourcing", "risk", "compliance"]
    assert plan.task("risk").depends_on == ["sourcing"]
    assert plan.task("compliance").depends_on == ["sourcing"]


def test_planner_rejects_dependency_cycle():
    """A cycle must not become an executable plan."""
    with pytest.raises(ValueError, match="cycle"):
        validate_task_plan(TaskPlan(tasks=[
            PlannerTask(task_id="a", agent="risk", depends_on=["b"]),
            PlannerTask(task_id="b", agent="sourcing", depends_on=["a"]),
        ]))


def test_planner_rejects_missing_dependency():
    """Every dependency must reference a task in the submitted plan."""
    with pytest.raises(ValueError, match="unknown dependency"):
        validate_task_plan(TaskPlan(tasks=[
            PlannerTask(task_id="risk", agent="risk", depends_on=["sourcing"]),
        ]))


def test_is_composite_request_requires_sourcing_and_analysis_keywords():
    """Sourcing-only messages must retain the existing sourcing route."""
    assert is_composite_request("帮我找电机供应商并评估风险") is True
    assert is_composite_request("帮我找电机供应商") is False
    assert is_composite_request("评估这家供应商的风险") is False


def test_router_prioritizes_composite_requests_over_existing_sourcing_rule():
    """Composite requests route to the supervisor before generic sourcing matches."""
    assert IntentRouter().route("帮我找电机供应商并评估风险和合规") == Intent.SUPERVISOR


def test_router_uses_existing_llm_fallback_for_non_deterministic_message(monkeypatch):
    """Messages without deterministic keywords keep the established LLM fallback."""
    router = IntentRouter()
    monkeypatch.setattr(router, "_classify_by_llm", lambda message: Intent.PARALLEL)

    assert router.route("请协助处理这件事") == Intent.PARALLEL
