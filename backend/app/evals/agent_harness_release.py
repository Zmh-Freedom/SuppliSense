"""Quality gates derived from persisted Agent Harness artifacts.

The release gate deliberately reads the PostgreSQL control plane after a run
has finished.  It does not trust a test runner's self-reported counters or
the wording of an answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from app.domains.agent_run.repo import get_run, list_events_after


_TERMINAL_STATUSES = {"COMPLETED", "PARTIAL", "NEEDS_REVIEW", "FAILED", "CANCELLED", "ACTION_FAILED"}
_TERMINAL_TASK_STATUSES = {"completed", "partial", "failed"}


@dataclass(frozen=True)
class PersistedHarnessMetrics:
    """Observable quality metrics reconstructed from one persisted run."""

    run_id: str
    run_status: str
    answer_status: str
    event_types: tuple[str, ...]
    tool_call_count: int
    persisted_evidence_count: int
    claim_count: int
    supported_claim_count: int
    evidence_coverage_ratio: float
    unresolved_tool_calls: int
    terminal_event_present: bool

    @property
    def claim_support_rate(self) -> float:
        if self.claim_count == 0:
            return 1.0 if self.answer_status in {"needs_review", "failed"} else 0.0
        return self.supported_claim_count / self.claim_count


def collect_persisted_harness_metrics(run_id: str) -> PersistedHarnessMetrics:
    """Build release metrics from the PG Run snapshot and durable event stream."""
    run = get_run(run_id)
    if not run:
        raise ValueError(f"Agent Harness run 不存在: {run_id}")
    snapshot = run.get("requirement") or {}
    if not isinstance(snapshot, dict):
        raise ValueError("Agent Harness 持久化快照格式无效")

    answer = snapshot.get("answer") or {}
    answer_status = str(answer.get("status") or "failed") if isinstance(answer, dict) else "failed"
    raw_tasks = snapshot.get("task_specs") or []
    unresolved = sum(
        1
        for task in raw_tasks
        if isinstance(task, dict) and str(task.get("status") or "pending") not in _TERMINAL_TASK_STATUSES
    )
    raw_outcomes = snapshot.get("tool_outcomes") or []
    outcome_count = sum(isinstance(item, dict) for item in raw_outcomes)
    raw_evidence = snapshot.get("evidence_records") or []
    evidence_count = sum(isinstance(item, dict) for item in raw_evidence)
    raw_claims = snapshot.get("validated_claims") or (
        answer.get("claims", []) if isinstance(answer, dict) else []
    )
    claims = [item for item in raw_claims if isinstance(item, dict)]
    supported = sum(item.get("validation_status") == "supported" for item in claims)
    coverage = snapshot.get("evidence_coverage") or {}
    try:
        coverage_ratio = float(coverage.get("coverage_ratio", 0.0))
    except (TypeError, ValueError):
        coverage_ratio = 0.0
    coverage_ratio = max(0.0, min(1.0, coverage_ratio))

    events = list_events_after(run_id)
    event_types = tuple(str(item.get("event_type")) for item in events if item.get("event_type"))
    return PersistedHarnessMetrics(
        run_id=run_id,
        run_status=str(run.get("status") or ""),
        answer_status=answer_status,
        event_types=event_types,
        tool_call_count=outcome_count,
        persisted_evidence_count=evidence_count,
        claim_count=len(claims),
        supported_claim_count=supported,
        evidence_coverage_ratio=coverage_ratio,
        unresolved_tool_calls=unresolved,
        terminal_event_present="done" in event_types,
    )


def assert_persisted_harness_quality(metrics: PersistedHarnessMetrics) -> None:
    """Fail closed when the durable run cannot prove a truthful terminal state."""
    failures: list[str] = []
    if metrics.run_status not in _TERMINAL_STATUSES:
        failures.append(f"run_status={metrics.run_status}")
    if metrics.answer_status not in {"completed", "partial", "needs_review", "failed"}:
        failures.append(f"answer_status={metrics.answer_status}")
    if metrics.unresolved_tool_calls:
        failures.append(f"unresolved_tool_calls={metrics.unresolved_tool_calls}")
    if not metrics.terminal_event_present:
        failures.append("terminal_event_missing=done")
    if metrics.answer_status == "completed" and metrics.evidence_coverage_ratio < 1.0:
        failures.append(f"completed_with_coverage={metrics.evidence_coverage_ratio}")
    if metrics.answer_status == "completed" and metrics.claim_support_rate < 1.0:
        failures.append(f"completed_with_claim_support={metrics.claim_support_rate}")
    if failures:
        raise AssertionError("Persisted Agent Harness quality gate failed: " + ", ".join(failures))


__all__ = [
    "PersistedHarnessMetrics",
    "assert_persisted_harness_quality",
    "collect_persisted_harness_metrics",
]
