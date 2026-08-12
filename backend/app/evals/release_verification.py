"""Callable release verification path backed by real production graph traces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.evals.sourcing_risk import (
    EvalTraceRecorder,
    ProductionGraphTraceAdapter,
    production_sourcing_risk_trace_adapter,
    run_sourcing_risk_evals,
)


class RecordedProductionTraceAdapter:
    """Replay only artifacts produced by a production GraphTraceAdapter."""

    def __init__(self, artifact: dict[str, Any]) -> None:
        if artifact.get("trace_source") != "production_graph":
            raise ValueError("release artifact must come from production_graph")
        traces = artifact.get("traces")
        if not isinstance(traces, list) or not traces:
            raise ValueError("release artifact must contain production traces")
        self._traces = {trace["case_id"]: trace for trace in traces}

    def run(self, case: dict[str, Any], recorder: EvalTraceRecorder) -> dict[str, Any]:
        trace = self._traces.get(case.get("id"))
        if not isinstance(trace, dict):
            raise ValueError(f"release artifact missing case {case.get('id')}")
        snapshot = trace.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError(f"release artifact case {case.get('id')} has no trace snapshot")
        recorder.load_snapshot(snapshot)
        result = snapshot.get("result")
        if not isinstance(result, dict) or not result:
            raise ValueError(f"release artifact case {case.get('id')} has no graph result")
        return result


def _record_production_traces(cases_path: str, artifact_path: str) -> dict[str, Any]:
    adapter: ProductionGraphTraceAdapter = production_sourcing_risk_trace_adapter()
    cases = _load_cases(cases_path)
    traces: list[dict[str, Any]] = []
    for case in cases:
        recorder = EvalTraceRecorder()
        observed = adapter.run(case, recorder)
        snapshot = recorder.snapshot()
        if snapshot.get("result") != observed:
            snapshot["result"] = dict(observed)
        traces.append({"case_id": case["id"], "snapshot": snapshot})
    artifact = {"artifact_version": "v1", "trace_source": "production_graph", "traces": traces}
    Path(artifact_path).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


def verify_sourcing_risk_release(cases_path: str, artifact_path: str) -> dict[str, Any]:
    """Run real graph executions, persist their trace, then consume that artifact in the Eval gate."""
    _record_production_traces(cases_path, artifact_path)
    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    report = run_sourcing_risk_evals(
        cases_path,
        trace_adapter=RecordedProductionTraceAdapter(artifact),
    )
    return {**report, "trace_source": "recorded_production_artifact", "trace_artifact": artifact_path}


def _load_cases(cases_path: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise ValueError("release Eval fixture must contain cases")
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Run production graph trace release verification")
    parser.add_argument("cases_path")
    parser.add_argument("artifact_path")
    args = parser.parse_args()
    print(json.dumps(verify_sourcing_risk_release(args.cases_path, args.artifact_path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
