import json
from pathlib import Path

from app.evals.release_verification import verify_sourcing_risk_release


def test_release_verification_writes_production_trace_artifact_and_consumes_it(tmp_path, monkeypatch) -> None:
    fixture = Path(__file__).parent / "evals" / "sourcing_risk_cases.json"
    artifact_path = tmp_path / "release-traces.json"
    calls: list[str] = []

    class ProductionAdapter:
        def run(self, case, recorder):
            calls.append(case["id"])
            recorder.record("start", at_ms=0)
            recorder.record("requirement_ready", at_ms=1)
            recorder.record("evidence_state", at_ms=1)
            recorder.record("end", at_ms=2)
            observed = {
                "requirement_status": case["expected"]["requirement_status"],
                "discovery_source": case["expected"]["discovery_source"],
                "external_imported": case["expected"]["external_imported"],
                "identity_status": case["expected"]["identity_status"],
                "score_eligible": case["expected"]["score_eligible"],
                "evidence_records": [],
                "citations": [],
                "approval": {"role": "none", "decision": "none", "proposal_status": "none", "write_count": 0, "replay_count": 0},
                "recovery_status": case["expected"]["recovery_status"],
                "recommended_recommendations": [],
            }
            return observed

    monkeypatch.setattr(
        "app.evals.release_verification.production_sourcing_risk_trace_adapter",
        lambda: ProductionAdapter(),
    )

    report = verify_sourcing_risk_release(str(fixture), str(artifact_path))

    assert artifact_path.exists()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["trace_source"] == "production_graph"
    assert len(artifact["traces"]) == 12
    assert report["trace_source"] == "recorded_production_artifact"
    assert report["case_count"] == 12
    assert calls == [trace["case_id"] for trace in artifact["traces"]]
