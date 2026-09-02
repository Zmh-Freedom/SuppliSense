"""Tests for bridging Harness action contracts to durable V2 proposals."""

from __future__ import annotations

import pytest
from langchain_core.tools import tool

from app.domains.agent_run.schemas import ApprovalDecisionRequest
from app.domains.sourcing_risk import action_service
from app.graphs.harness.actions import ActionGate
from app.graphs.harness.durable_actions import (
    persist_action_proposal,
    proposal_from_durable_row,
)
from app.tools.executor import ToolContext, ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec


def _watchlist_gate() -> ActionGate:
    @tool
    def add_to_watchlist(company_name: str, target_source: str) -> dict:
        """Test-only monitoring write."""
        return {"success": True, "side_effect_receipt": {"receipt_id": "r-1"}}

    registry = ToolRegistry()
    registry.register(
        add_to_watchlist,
        ToolSpec(
            name="add_to_watchlist",
            capability="risk_monitoring",
            side_effect="write",
            approval_policy="required",
        ),
    )
    return ActionGate(ToolExecutor(registry), secret_key="test-secret")


def test_persist_action_proposal_keeps_harness_binding_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _watchlist_gate()
    proposal = gate.propose(
        "add_to_watchlist",
        {"company_name": "甲公司", "target_source": "conversation_state"},
        ToolContext(session_id="session-1", run_id="run-1", user_id="requester"),
    )
    captured: dict = {}

    def fake_create(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"id": proposal.proposal_id, "run_id": proposal.run_id, "payload": args[2], "idempotency_key": args[3], "status": "pending"}

    monkeypatch.setattr(
        "app.domains.sourcing_risk.action_service.create_action_proposal",
        fake_create,
    )
    result = persist_action_proposal(proposal, user_role="analyst", expected_version=1)

    assert result["id"] == proposal.proposal_id
    assert captured["args"][1] == "add_watchlist"
    metadata = captured["args"][2]["_harness_action"]
    assert metadata["action_hash"] == proposal.action_hash
    assert metadata["session_id"] == "session-1"
    assert captured["args"][3] == proposal.idempotency_key


def test_durable_proposal_rehydrates_and_token_verification_uses_persisted_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _watchlist_gate()
    proposal = gate.propose(
        "add_to_watchlist",
        {"company_name": "甲公司", "target_source": "conversation_state"},
        ToolContext(session_id="session-1", run_id="run-1", user_id="requester"),
    )
    row = {
        "id": proposal.proposal_id,
        "run_id": proposal.run_id,
        "status": "pending",
        "idempotency_key": proposal.idempotency_key,
        "payload": {
            **proposal.arguments,
            "_harness_action": {
                "tool_name": proposal.tool_name,
                "action_hash": proposal.action_hash,
                "session_id": proposal.session_id,
                "expires_at": proposal.expires_at.isoformat(),
            },
        },
    }
    restored = proposal_from_durable_row(row, session_id="session-1", user_id="requester")
    approved, token = gate.approve(restored, "approver-1")
    monkeypatch.setattr(action_service.settings, "SECRET_KEY", "test-secret")

    action_service._verify_harness_approval(
        row,
        {"session_id": "session-1", "user_id": "requester"},
        token,
        "approver-1",
    )
    assert approved.status == "approved"


def test_durable_harness_approval_rejects_changed_persisted_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _watchlist_gate()
    proposal = gate.propose(
        "add_to_watchlist",
        {"company_name": "甲公司", "target_source": "conversation_state"},
        ToolContext(session_id="session-1", run_id="run-1", user_id="requester"),
    )
    _, token = gate.approve(proposal, "approver-1")
    row = {
        "id": proposal.proposal_id,
        "run_id": proposal.run_id,
        "status": "pending",
        "idempotency_key": proposal.idempotency_key,
        "payload": {
            "company_name": "乙公司",
            "target_source": "conversation_state",
            "_harness_action": {
                "tool_name": proposal.tool_name,
                "action_hash": proposal.action_hash,
                "session_id": proposal.session_id,
                "expires_at": proposal.expires_at.isoformat(),
            },
        },
    }
    monkeypatch.setattr(action_service.settings, "SECRET_KEY", "test-secret")

    with pytest.raises(Exception) as exc_info:
        action_service._verify_harness_approval(
            row,
            {"session_id": "session-1", "user_id": "requester"},
            token,
            "approver-1",
        )
    assert "动作摘要校验失败" in str(exc_info.value)


def test_approval_request_accepts_optional_recovery_token() -> None:
    request = ApprovalDecisionRequest(
        expected_version=1,
        decision="approved",
        approval_token="h1.recovery-token",
    )

    assert request.approval_token == "h1.recovery-token"
