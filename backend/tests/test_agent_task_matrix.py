import pytest

from app.graphs.agent_core.planner import plan_supplier_analysis_task


def test_task_matrix_creates_unique_subtask_for_each_supplier_and_dimension():
    suppliers = [f"供应商{i}" for i in range(1, 8)]

    task = plan_supplier_analysis_task(
        task_id="analysis-1",
        supplier_names=suppliers,
        dimensions=["risk", "esg", "sentiment"],
    )

    assert len(task.subtasks) == 21
    assert len({subtask.subtask_id for subtask in task.subtasks}) == 21
    assert len({(subtask.supplier_name, subtask.dimension) for subtask in task.subtasks}) == 21
    assert all(subtask.status == "pending" for subtask in task.subtasks)
    assert all(subtask.required for subtask in task.subtasks if subtask.dimension != "sentiment")
    assert all(not subtask.required for subtask in task.subtasks if subtask.dimension == "sentiment")


def test_task_matrix_makes_analysis_depend_on_sourcing_when_requested():
    task = plan_supplier_analysis_task(
        task_id="analysis-1",
        supplier_names=["甲公司"],
        dimensions=["risk", "compliance"],
        include_sourcing=True,
    )

    sourcing = next(subtask for subtask in task.subtasks if subtask.dimension == "sourcing")
    analysis = [subtask for subtask in task.subtasks if subtask.dimension != "sourcing"]

    assert sourcing.depends_on == []
    assert all(subtask.depends_on == [sourcing.subtask_id] for subtask in analysis)
    assert all(subtask.evidence_requirements for subtask in analysis)


def test_task_matrix_rejects_duplicate_dimensions_and_dependency_cycles():
    with pytest.raises(ValueError, match="duplicate analysis dimension"):
        plan_supplier_analysis_task(
            task_id="analysis-1",
            supplier_names=["甲公司"],
            dimensions=["risk", "risk"],
        )
