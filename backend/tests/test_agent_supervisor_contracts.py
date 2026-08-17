import pytest
from pydantic import ValidationError

from app.graphs.agent_supervisor.contracts import (
    AgentFinding,
    AgentResult,
    PendingApproval,
    RecommendedAction,
)


def test_agent_result_rejects_missing_evidence_for_completed_finding():
    with pytest.raises(ValidationError):
        AgentResult(
            agent="risk",
            status="completed",
            summary="存在高风险",
            findings=[AgentFinding(type="judicial_risk", level="high", title="重大诉讼")],
            evidence=[],
        )


def test_agent_result_accepts_completed_finding_with_evidence_reference():
    result = AgentResult(
        agent="risk",
        status="completed",
        summary="存在高风险",
        findings=[
            AgentFinding(
                type="judicial_risk",
                level="high",
                title="重大诉讼",
                evidence_ids=["e-1"],
            )
        ],
        evidence=[
            {
                "evidence_id": "e-1",
                "source": "裁判文书网",
                "source_type": "official",
                "freshness": "fresh",
                "confidence": 0.95,
            }
        ],
    )

    assert result.findings[0].evidence_ids == ["e-1"]


def test_pending_approval_marks_every_mutating_action():
    approval = PendingApproval(
        approval_id="a-1",
        action_type="add_to_watchlist",
        target={"company_id": "c-1"},
        reason="风险上升",
        impact="进入监控",
        status="pending",
    )

    assert approval.requires_approval is True


def test_pending_approval_cannot_be_overridden_by_caller():
    approval = PendingApproval(
        approval_id="a-1",
        action_type="send_risk_notification",
        target={"company_id": "c-1"},
        reason="风险上升",
        impact="发送通知",
        status="pending",
        requires_approval=False,
    )

    assert approval.requires_approval is True


def test_pending_approval_stays_required_after_post_construction_assignment():
    approval = PendingApproval(
        approval_id="a-1",
        action_type="add_to_watchlist",
        target={"company_id": "c-1"},
        reason="风险上升",
        impact="进入监控",
        status="pending",
    )

    approval.requires_approval = False

    assert approval.requires_approval is True


def test_recommended_action_stays_required_after_post_construction_assignment():
    action = RecommendedAction(
        action_type="add_to_watchlist",
        target={"company_id": "c-1"},
        reason="风险上升",
    )

    action.requires_approval = False

    assert action.requires_approval is True


def test_agent_result_rejects_unknown_status():
    with pytest.raises(ValidationError):
        AgentResult(agent="risk", status="done", summary="完成")
