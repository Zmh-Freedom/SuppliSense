"""Recovery must bind checkpoint identity and retain committed business results."""
import pytest
from app.graphs.checkpoint_recovery import validate_checkpoint
from app.domains.agent_run.coordinator import ExecutionLost


def record():
    return {"execution_input": {"run_id": "run", "session_id": "session", "user_id": "user", "user_message": "query", "runtime": "agent-supervisor"}, "snapshot": {"plan": {"tasks": []}, "agent_results": {"risk": {"score": 90}}}}


def values():
    from app.graphs.checkpoint_recovery import input_digest
    return {"run_id": "run", "session_id": "session", "user_id": "user", "input_digest": input_digest(record()["execution_input"]), **record()["snapshot"]}


def test_checkpoint_keeps_committed_results():
    validate_checkpoint(record(), values())


@pytest.mark.parametrize("field,changed", [("run_id", "other"), ("user_id", "other"), ("input_digest", "forged"), ("agent_results", {}), ("plan", {"tasks": [{"id": "extra"}]})])
def test_checkpoint_rejects_identity_plan_or_receipt_mismatch(field, changed):
    with pytest.raises(ExecutionLost):
        validate_checkpoint(record(), {**values(), field: changed})


def test_harness_restores_pg_committed_results_into_an_older_checkpoint():
    from app.graphs.checkpoint_recovery import reconcile_checkpoint, input_digest
    frozen = {**record()["execution_input"], "runtime": "harness"}
    task = {"task_id": "risk", "tool_name": "assess_risk", "arguments": {"company_name": "甲"}, "status": "pending", "attempts": 0}
    checkpoint = {"run_id": "run", "session_id": "session", "user_id": "user", "input_digest": input_digest(frozen), "task_specs": [task], "tool_outcomes": [], "tool_call_count": 0}
    ledger = {**checkpoint, "task_specs": [{**task, "status": "completed", "attempts": 1}], "tool_outcomes": [{"call_id": "persisted", "status": "success"}], "tool_call_count": 1}
    restored = reconcile_checkpoint({"execution_input": frozen, "snapshot": ledger}, checkpoint)
    assert restored["tool_outcomes"] == ledger["tool_outcomes"]
    assert restored["tool_call_count"] == 1
    assert restored["task_specs"][0]["status"] == "completed"


def test_harness_reconciliation_never_accepts_modified_tool_arguments():
    from app.graphs.checkpoint_recovery import reconcile_checkpoint, input_digest
    frozen = {**record()["execution_input"], "runtime": "harness"}
    checkpoint = {"run_id": "run", "session_id": "session", "user_id": "user", "input_digest": input_digest(frozen), "task_specs": [{"task_id": "risk", "tool_name": "assess_risk", "arguments": {"company_name": "乙"}}]}
    ledger = {**checkpoint, "task_specs": [{"task_id": "risk", "tool_name": "assess_risk", "arguments": {"company_name": "甲"}}]}
    with pytest.raises(ExecutionLost):
        reconcile_checkpoint({"execution_input": frozen, "snapshot": ledger}, checkpoint)


def test_harness_resume_charges_started_calls_even_without_a_result():
    from app.graphs.checkpoint_recovery import reconcile_checkpoint, input_digest
    frozen = {**record()["execution_input"], "runtime": "harness"}
    checkpoint = {"run_id": "run", "session_id": "session", "user_id": "user", "input_digest": input_digest(frozen), "task_specs": [], "tool_call_count": 1}
    restored = reconcile_checkpoint({"execution_input": frozen, "snapshot": checkpoint, "reserved_tool_calls": 2}, checkpoint)
    assert restored["tool_call_count"] == 2
