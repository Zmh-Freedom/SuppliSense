import json
from pathlib import Path

from app.evals.release_verification import verify_sourcing_risk_release
from app.graphs.sourcing_risk_v2.trace import GraphTraceRecorder


def test_release_verification_uses_production_recorder_and_adapter_chain(tmp_path, monkeypatch) -> None:
    source_fixture = Path(__file__).parent / "evals" / "sourcing_risk_cases.json"
    cases = json.loads(source_fixture.read_text(encoding="utf-8"))
    for case in cases["cases"]:
        case["input"]["run_id"] = f"run-{case['id']}"
    fixture = tmp_path / "cases.json"
    fixture.write_text(json.dumps(cases), encoding="utf-8")
    artifact_path = tmp_path / "release-traces.json"
    recorders: list[GraphTraceRecorder] = []

    def execute(case, recorder):
        recorders.append(recorder)
        recorder.record("start", at_ms=0)
        expected = case["expected"]
        recorder.record(
            "clarification" if expected["requirement_status"] == "clarification_required" else "requirement_ready",
            at_ms=1,
        )
        if expected["discovery_source"] == "local_and_external":
            recorder.record("external_staged", at_ms=1)
        if expected["external_imported"]:
            recorder.record("approval_decision", at_ms=1)
            recorder.record("external_import", at_ms=1)
        if case["id"] == "action-approval-replay":
            recorder.record("approval_decision", at_ms=1)
            recorder.record("replay", at_ms=1)
            recorder.record("action_effect", at_ms=1)
        if case["id"] == "recovery-restart-resume":
            recorder.record("checkpoint_saved", at_ms=1)
            recorder.record("restart", at_ms=1)
            recorder.record("resume", at_ms=1)
        recorder.record("evidence_state", at_ms=1)
        recorder.record("end", at_ms=2)
        refs = expected.get("expected_evidence_refs", expected.get("evidence_refs", []))
        statuses = expected.get("expected_evidence_statuses", {})
        evidence_records = [
            {"ref": ref, "claim_id": f"claim-{ref}", "status": statuses.get(ref, "available")}
            for ref in refs
        ]
        result = {
            "requirement_status": expected["requirement_status"], "discovery_source": expected["discovery_source"],
            "external_imported": expected["external_imported"], "identity_status": expected["identity_status"],
            "score_eligible": expected["score_eligible"], "evidence_records": evidence_records,
            "citations": [
                {"claim_id": f"claim-{ref}", "evidence_ref": ref}
                for ref in refs if statuses.get(ref, "available") == "available"
            ],
            "approval": {"role": case["input"].get("approval_role", "none"), "decision": case["input"].get("approval_decision", "none"), "proposal_status": case["input"].get("proposal_status", "none"), "idempotency_key": case["input"].get("idempotency_key"), "write_count": 0, "replay_count": 0},
            "recovery_status": expected["recovery_status"], "recommended_recommendations": case.get("recommended_recommendations", []),
        }
        recorder.set_result(result)
        return recorder.snapshot()

    monkeypatch.setattr("app.graphs.sourcing_risk_v2.runner.execute_sourcing_risk_graph_for_eval", execute)

    report = verify_sourcing_risk_release(str(fixture), str(artifact_path))

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["trace_source"] == "production_graph"
    assert len(artifact["traces"]) == 12
    assert report["trace_source"] == "recorded_production_artifact"
    assert report["case_count"] == 12
    assert len(recorders) == 12
    assert all(isinstance(recorder, GraphTraceRecorder) for recorder in recorders)
