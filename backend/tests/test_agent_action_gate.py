"""Fail-closed Harness proposal and approval token tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.tools import tool

from app.graphs.harness.actions import ActionGate
from app.tools.executor import ToolContext, ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec


def _write_executor(result: dict) -> ToolExecutor:
    @tool
    def write_company(company_name: str) -> dict:
        """Write a company for tests."""
        return {"company_name": company_name, **result}

    registry = ToolRegistry()
    registry.register(
        write_company,
        ToolSpec(
            name="write_company",
            capability="test",
            side_effect="write",
            approval_policy="required",
        ),
    )
    return ToolExecutor(registry)


def _context() -> ToolContext:
    return ToolContext(session_id="session-1", run_id="run-1", user_id="requester")


def _watchlist_like_executor() -> ToolExecutor:
    @tool
    def add_to_watchlist(
        company_name: str = "",
        target_source: str = "conversation_state",
        target_type: str | None = None,
        monitor_target_id: str | None = None,
        supplier_id: str | None = None,
        candidate_id: str | None = None,
        company_id: str | None = None,
        supplier_code: str | None = None,
    ) -> dict:
        """Add a supplier to the monitoring list."""
        return {
            "company_name": company_name,
            "target_type": target_type,
            "side_effect_receipt": {"receipt_id": "r-watchlist"},
        }

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
    return ToolExecutor(registry)


def test_action_gate_creates_stable_proposal_without_calling_tool() -> None:
    called = False
    executor = _write_executor({"side_effect_receipt": {"receipt_id": "r-1"}})
    gate = ActionGate(executor, secret_key="test-secret")

    first = gate.propose("write_company", {"company_name": "甲公司"}, _context())
    second = gate.propose("write_company", {"company_name": "甲公司"}, _context())

    assert first.proposal_id == second.proposal_id
    assert first.action_hash == second.action_hash
    assert first.idempotency_key == second.idempotency_key
    assert first.arguments == second.arguments
    assert first.status == "pending"
    assert first.idempotency_key.startswith("harness:run-1:")
    assert called is False


def test_action_gate_binds_approval_to_proposal_and_returns_receipt() -> None:
    executor = _write_executor({"side_effect_receipt": {"receipt_id": "r-1"}})
    gate = ActionGate(executor, secret_key="test-secret")
    proposal = gate.propose("write_company", {"company_name": "甲公司"}, _context())
    approved, token = gate.approve(proposal, "approver-1")

    outcome = asyncio.run(gate.execute(approved, token, approver_id="approver-1"))

    assert outcome.status == "success"
    assert outcome.side_effect_receipt == {"receipt_id": "r-1"}


def test_action_gate_normalizes_optional_arguments_before_approval_hash() -> None:
    executor = _watchlist_like_executor()
    gate = ActionGate(executor, secret_key="test-secret")
    proposal = gate.propose(
        "add_to_watchlist",
        {"company_name": "北京经纬恒润科技股份有限公司", "target_source": "conversation_state"},
        _context(),
    )

    assert proposal.arguments["target_type"] is None
    approved, token = gate.approve(proposal, "approver-1")
    outcome = asyncio.run(gate.execute(approved, token, approver_id="approver-1"))

    assert outcome.status == "success"
    assert outcome.error is None
    assert outcome.side_effect_receipt == {"receipt_id": "r-watchlist"}


def test_action_gate_rejects_tampered_or_wrong_approver_token() -> None:
    executor = _write_executor({"side_effect_receipt": {"receipt_id": "r-1"}})
    gate = ActionGate(executor, secret_key="test-secret")
    proposal = gate.propose("write_company", {"company_name": "甲公司"}, _context())
    approved, token = gate.approve(proposal, "approver-1")

    with pytest.raises(ValueError, match="签名无效"):
        parts = token.split(".")
        tampered = ".".join([parts[0], parts[1], ("A" if parts[2][0] != "A" else "B") + parts[2][1:]])
        asyncio.run(gate.execute(approved, tampered, approver_id="approver-1"))
    with pytest.raises(ValueError, match="审批人不匹配"):
        asyncio.run(gate.execute(approved, token, approver_id="approver-2"))


def test_action_gate_rejects_expired_proposal_and_token() -> None:
    executor = _write_executor({"side_effect_receipt": {"receipt_id": "r-1"}})
    gate = ActionGate(executor, secret_key="test-secret")
    now = datetime.now(timezone.utc)
    proposal = gate.propose(
        "write_company",
        {"company_name": "甲公司"},
        _context(),
        ttl_seconds=1,
        now=now,
    )

    with pytest.raises(ValueError, match="已过期"):
        gate.approve(proposal, "approver-1", now=now + timedelta(seconds=1))


def test_action_gate_does_not_claim_success_without_side_effect_receipt() -> None:
    executor = _write_executor({"success": True})
    gate = ActionGate(executor, secret_key="test-secret")
    proposal = gate.propose("write_company", {"company_name": "甲公司"}, _context())
    approved, token = gate.approve(proposal, "approver-1")

    outcome = asyncio.run(gate.execute(approved, token, approver_id="approver-1"))

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.code == "side_effect_receipt_missing"
