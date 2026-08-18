"""Deterministic supplier-by-dimension task matrix planning."""

from __future__ import annotations

from app.graphs.agent_core.contracts import AgentSubtask, AgentTask


_OPTIONAL_DIMENSIONS = {"sentiment"}
_EVIDENCE_REQUIREMENTS = {
    "risk": ["risk"],
    "esg": ["esg"],
    "sentiment": ["sentiment"],
    "compliance": ["compliance"],
}


def plan_supplier_analysis_task(
    *,
    task_id: str,
    supplier_names: list[str],
    dimensions: list[str],
    include_sourcing: bool = False,
) -> AgentTask:
    """Build a stable, acyclic supplier × dimension matrix for one user task."""
    normalized_suppliers = _unique_nonempty(supplier_names)
    normalized_dimensions = _unique_nonempty(dimensions)
    if len(normalized_dimensions) != len(dimensions):
        raise ValueError("duplicate analysis dimension")

    subtasks: list[AgentSubtask] = []
    sourcing_id = "sourcing"
    if include_sourcing:
        subtasks.append(
            AgentSubtask(
                subtask_id=sourcing_id,
                supplier_name=None,
                dimension="sourcing",
                evidence_requirements=["supplier_candidate"],
            )
        )
    for supplier_index, supplier_name in enumerate(normalized_suppliers, start=1):
        for dimension_index, dimension in enumerate(normalized_dimensions, start=1):
            subtasks.append(
                AgentSubtask(
                    subtask_id=f"{task_id}-{supplier_index}-{dimension_index}",
                    supplier_name=supplier_name,
                    dimension=dimension,
                    depends_on=[sourcing_id] if include_sourcing else [],
                    required=dimension not in _OPTIONAL_DIMENSIONS,
                    evidence_requirements=_EVIDENCE_REQUIREMENTS.get(dimension, [dimension]),
                )
            )
    return AgentTask(
        task_id=task_id,
        task_type="analysis",
        target_supplier_names=normalized_suppliers,
        analysis_dimensions=normalized_dimensions,
        subtasks=subtasks,
    )


def _unique_nonempty(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))
