"""Deterministic planning for composite sourcing and analysis requests."""

import re

from app.graphs.agent_supervisor.contracts import PlannerTask, TaskPlan


_SOURCING_KEYWORDS = ("找供应商", "寻源", "采购", "替代")
_ANALYSIS_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("risk", ("风险",)),
    ("esg", ("ESG", "esg", "环境社会治理")),
    ("compliance", ("合规",)),
    ("sentiment", ("舆情",)),
)
_SUPPORTED_AGENTS = {"sourcing", "risk", "esg", "compliance", "sentiment"}


def _has_sourcing_request(message: str) -> bool:
    return (
        any(keyword in message for keyword in _SOURCING_KEYWORDS)
        or ("找" in message and "供应商" in message)
    )


def fallback_requirement_from_query(message: str) -> dict[str, str] | None:
    """Build a conservative local-search requirement for conversational requests.

    The dedicated requirement parser intentionally requires a specification. The
    chat Supervisor can still perform a read-only broad search when the user only
    names a product/category, while leaving all write decisions behind review.
    """
    match = re.search(r"(?:找|推荐|寻找)(.+?)(?:供应商|厂家|厂商)", message)
    if not match:
        return None
    target = match.group(1).strip(" ，,、")
    if not target:
        return None
    regions = ("华东", "华南", "华北", "西南", "西北", "东北")
    region = next((value for value in regions if target.startswith(value)), None)
    category = target[len(region):] if region else target
    category = category.strip() or target
    return {
        "category": category,
        "specification": category,
        **({"region": region} if region else {}),
    }


def is_composite_request(message: str) -> bool:
    """Return whether a message needs coordinated sourcing or multi-risk work."""
    has_sourcing = _has_sourcing_request(message)
    requested_analysis_count = sum(
        any(keyword in message for keyword in keywords)
        for _, keywords in _ANALYSIS_KEYWORDS
    )
    asks_for_monitoring = (
        "监控" in message
        and "监控清单" not in message
        and requested_analysis_count > 0
    )
    return (has_sourcing and requested_analysis_count > 0) or requested_analysis_count >= 2 or asks_for_monitoring


def plan_agent_task(user_query: str, intent: dict | None = None) -> TaskPlan:
    """Create a deterministic plan from sourcing and analysis keywords."""
    del intent
    requested_agents = [
        agent
        for agent, keywords in _ANALYSIS_KEYWORDS
        if any(keyword in user_query for keyword in keywords)
    ]
    has_sourcing = _has_sourcing_request(user_query)

    tasks: list[PlannerTask] = []
    if has_sourcing:
        tasks.append(PlannerTask(task_id="sourcing", agent="sourcing"))
    for agent in requested_agents:
        depends_on = ["sourcing"] if has_sourcing else []
        tasks.append(PlannerTask(task_id=agent, agent=agent, depends_on=depends_on))

    return validate_task_plan(TaskPlan(tasks=tasks))


def validate_task_plan(plan: TaskPlan) -> TaskPlan:
    """Validate supported agents, task references, unique IDs, and DAG structure."""
    task_ids = [task.task_id for task in plan.tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("duplicate task_id")

    for task in plan.tasks:
        if task.agent not in _SUPPORTED_AGENTS:
            raise ValueError(f"unknown agent: {task.agent}")
        unknown_dependencies = set(task.depends_on) - set(task_ids)
        if unknown_dependencies:
            raise ValueError(
                "unknown dependency: " + ", ".join(sorted(unknown_dependencies))
            )

    dependencies = {task.task_id: task.depends_on for task in plan.tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError("dependency cycle")
        if task_id in visited:
            return

        visiting.add(task_id)
        for dependency in dependencies[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in task_ids:
        visit(task_id)
    return plan
