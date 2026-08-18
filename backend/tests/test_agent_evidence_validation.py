"""Tests for bounded evidence coverage and validation decisions."""

from datetime import datetime, timezone

from app.graphs.agent_core.contracts import LoopState
from app.graphs.agent_core.validator import validate_evidence
from app.graphs.agent_supervisor.contracts import (
    AgentResult,
    EvidenceMergeResult,
    PlannerTask,
    TaskPlan,
)


def _loop_state(*, evidence_count_before: int = 0) -> LoopState:
    return LoopState(
        loop_type="evidence",
        iteration=1,
        max_iterations=2,
        tool_call_count=1,
        max_tool_calls=2,
        started_at=datetime.now(timezone.utc),
        timeout_seconds=30,
        evidence_count_before=evidence_count_before,
    )


def _plan() -> TaskPlan:
    return TaskPlan(tasks=[
        PlannerTask(task_id="risk", agent="risk", required=True),
        PlannerTask(task_id="sentiment", agent="sentiment", required=True),
        PlannerTask(task_id="compliance", agent="compliance", required=False),
    ])


def _evidence(evidence_id: str, dimension: str, claim: str = "正常") -> dict:
    return {
        "evidence_id": evidence_id,
        "source": "可信来源",
        "source_type": "official",
        "freshness": "fresh",
        "confidence": 0.9,
        "dimension": dimension,
        "claim": claim,
    }


def test_validation_calculates_required_coverage_from_actual_evidence_dimensions():
    """A completed worker without its required dimension evidence is insufficient."""
    merged = EvidenceMergeResult(evidence=[_evidence("risk-1", "risk")])
    results = {
        "risk": AgentResult(agent="risk", status="completed", summary="完成", evidence=[_evidence("risk-1", "risk")]),
        "sentiment": AgentResult(agent="sentiment", status="completed", summary="完成"),
    }

    validation = validate_evidence(merged, _plan(), results, _loop_state())

    assert validation.coverage == 0.5
    assert validation.covered_dimensions == ["risk"]
    assert validation.insufficient_dimensions == ["sentiment"]
    assert validation.status == "partial"
    assert "sentiment" in validation.limitations[0]


def test_validation_conflict_removes_dimension_from_usable_coverage_and_requires_review():
    """Conflicting required evidence must never qualify for a deterministic decision."""
    conflict = [_evidence("risk-clear", "risk", "无重大风险"), _evidence("risk-hit", "risk", "存在重大风险")]
    merged = EvidenceMergeResult(
        evidence=[*conflict, _evidence("sentiment-1", "sentiment")],
        conflicts=[conflict],
        requires_review=True,
    )
    results = {
        "risk": AgentResult(agent="risk", status="completed", summary="完成", evidence=conflict),
        "sentiment": AgentResult(agent="sentiment", status="completed", summary="完成", evidence=[_evidence("sentiment-1", "sentiment")]),
    }

    validation = validate_evidence(merged, _plan(), results, _loop_state(evidence_count_before=1))

    assert validation.coverage == 0.5
    assert validation.conflict_dimensions == ["risk"]
    assert validation.status == "needs_review"
    assert validation.can_recommend is False


def test_validation_stops_when_remediation_adds_no_evidence():
    """The evidence loop must exit instead of repeating an identical remediation."""
    validation = validate_evidence(
        EvidenceMergeResult(missing_dimensions=["risk"], requires_review=True),
        _plan(),
        {"risk": AgentResult(agent="risk", status="needs_review", summary="无数据")},
        _loop_state(),
        remediation_attempts=1,
    )

    assert validation.status == "needs_review"
    assert validation.stop_reason == "no_new_evidence"
    assert validation.should_remediate is False


def test_validation_allows_only_one_remediation_before_partial_outcome():
    """Validation may request one repair, then must return a bounded partial result."""
    risk_evidence = _evidence("risk-1", "risk")
    merged = EvidenceMergeResult(
        evidence=[risk_evidence],
        missing_dimensions=["sentiment"],
        requires_review=True,
    )
    results = {
        "risk": AgentResult(agent="risk", status="completed", summary="完成", evidence=[risk_evidence]),
        "sentiment": AgentResult(agent="sentiment", status="needs_review", summary="无数据"),
    }

    first = validate_evidence(merged, _plan(), results, _loop_state(evidence_count_before=0))
    second = validate_evidence(merged, _plan(), results, _loop_state(evidence_count_before=0), remediation_attempts=1)

    assert first.should_remediate is True
    assert second.should_remediate is False
    assert second.status == "partial"
    assert second.stop_reason == "validation_remediation_exhausted"
