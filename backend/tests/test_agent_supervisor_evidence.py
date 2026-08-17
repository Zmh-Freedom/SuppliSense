"""Behavior tests for Supervisor evidence merging and decision safety."""

import pytest

from app.graphs.agent_supervisor.contracts import AgentFinding, AgentResult, PlannerTask, TaskPlan
from app.graphs.agent_supervisor.decision import build_decision
from app.graphs.agent_supervisor.evidence import merge_evidence


def _evidence(
    evidence_id: str,
    *,
    source: str,
    source_type: str,
    freshness: str,
    confidence: float,
    claim: str = "未发现制裁记录",
) -> dict:
    return {
        "evidence_id": evidence_id,
        "source": source,
        "source_type": source_type,
        "freshness": freshness,
        "confidence": confidence,
        "company_id": "company-1",
        "dimension": "sanctions",
        "claim": claim,
    }


def _completed_result(*evidence: dict, actions: list[dict] | None = None) -> AgentResult:
    return AgentResult(
        agent="risk",
        status="completed",
        summary="风险评估完成",
        evidence=list(evidence),
        recommended_actions=actions or [],
    )


def test_merge_evidence_prefers_official_fresh_evidence():
    """Ranking a duplicate key incorrectly could hide the best available source."""
    result = merge_evidence({
        "risk": _completed_result(
            _evidence("third-party-stale", source="风险数据库", source_type="third_party", freshness="stale", confidence=0.99),
            _evidence("official-fresh", source="国家企业信用信息公示系统", source_type="official", freshness="fresh", confidence=0.60),
        )
    })

    assert [item.evidence_id for item in result.evidence] == [
        "official-fresh",
        "third-party-stale",
    ]
    assert result.evidence[0].source_type == "official"
    assert result.evidence[0].freshness == "fresh"


def test_conflicting_evidence_requires_review_and_preserves_claims():
    """Dropping either side of a conflicting claim could create a false-clear decision."""
    result = merge_evidence({
        "risk": _completed_result(
            _evidence("official-clear", source="裁判文书网", source_type="official", freshness="fresh", confidence=0.95, claim="不存在重大诉讼"),
            _evidence("official-hit", source="裁判文书网", source_type="official", freshness="fresh", confidence=0.90, claim="存在重大诉讼"),
        )
    })

    assert result.requires_review is True
    assert len(result.conflicts) == 1
    assert {item.claim for item in result.conflicts[0]} == {"不存在重大诉讼", "存在重大诉讼"}
    assert {item.evidence_id for item in result.evidence} == {"official-clear", "official-hit"}


def test_conflicting_evidence_still_deduplicates_repeated_claims():
    """A conflict must retain distinct claims, but not duplicate the same claim."""
    result = merge_evidence({
        "risk": _completed_result(
            _evidence("clear-low", source="裁判文书网", source_type="official", freshness="fresh", confidence=0.50, claim="不存在重大诉讼"),
            _evidence("clear-high", source="裁判文书网", source_type="official", freshness="fresh", confidence=0.95, claim="不存在重大诉讼"),
            _evidence("hit", source="裁判文书网", source_type="official", freshness="fresh", confidence=0.90, claim="存在重大诉讼"),
        )
    })

    assert {item.evidence_id for item in result.evidence} == {"clear-high", "hit"}


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_failed_or_skipped_required_agent_is_reported_as_missing_dimension(status):
    """Ignoring a required task failure would let decisions treat absent evidence as complete."""
    failed = AgentResult(agent="compliance", status=status, summary="合规数据不可用")

    result = merge_evidence(
        {"compliance": failed},
        TaskPlan(tasks=[PlannerTask(task_id="compliance", agent="compliance", required=True)]),
    )

    assert result.missing_dimensions == ["compliance"]
    assert result.requires_review is True


def test_decision_creates_approval_without_executing_write(monkeypatch):
    """A watchlist suggestion must stay pending until a human approves it."""
    def forbidden_write(*args, **kwargs):
        raise AssertionError("decision layer must not execute writes")

    monkeypatch.setattr("app.domains.alert.service.add_to_watchlist", forbidden_write)
    state = {
        "recommendations": [{
            "action_type": "add_to_watchlist",
            "target": {"company_id": "company-1"},
            "reason": "风险等级上升",
            "impact": "进入持续监控",
        }],
    }

    decision = build_decision(state, merge_evidence({"risk": _completed_result()}))

    assert decision.pending_approvals[0].action_type == "add_to_watchlist"
    assert decision.pending_approvals[0].status == "pending"


def test_review_required_evidence_prevents_deterministic_recommendation():
    """Turning uncertain evidence into an automatic recommendation bypasses human review."""
    state = {
        "recommendations": [{
            "action_type": "add_to_watchlist",
            "target": {"company_id": "company-1"},
            "reason": "风险等级上升",
            "impact": "进入持续监控",
        }],
        "findings": [AgentFinding(type="sanctions", level="high", title="制裁命中").model_dump()],
    }
    merged = merge_evidence({"compliance": AgentResult(agent="compliance", status="failed", summary="合规数据不可用")})

    decision = build_decision(state, merged)

    assert decision.requires_review is True
    assert decision.recommendations == []
    assert decision.pending_approvals == []


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_failed_or_skipped_optional_agent_does_not_block_decision(status):
    """Optional task degradation must not suppress a clear recommendation."""
    state = {
        "recommendations": [{
            "action_type": "add_to_watchlist",
            "target": {"company_id": "company-1"},
            "reason": "风险等级上升",
            "impact": "进入持续监控",
        }],
    }
    results = {
        "risk": _completed_result(),
        "sentiment": AgentResult(agent="sentiment", status=status, summary="舆情数据不可用"),
    }

    merged = merge_evidence(
        results,
        TaskPlan(tasks=[
            PlannerTask(task_id="risk", agent="risk", required=True),
            PlannerTask(task_id="sentiment", agent="sentiment", required=False),
        ]),
    )
    decision = build_decision(state, merged)

    assert merged.missing_dimensions == []
    assert merged.requires_review is False
    assert decision.pending_approvals[0].action_type == "add_to_watchlist"
