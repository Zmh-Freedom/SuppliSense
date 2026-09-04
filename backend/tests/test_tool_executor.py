"""Tool Registry and ToolExecutor contract tests."""

import asyncio

from langchain_core.tools import tool

from app.graphs.harness.actions import ActionGate
from app.tools import TOOLS_LIST, TOOL_REGISTRY
from app.tools.executor import ToolContext, ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec


def test_default_registry_has_unique_pydantic_contracts_and_policies() -> None:
    definitions = TOOL_REGISTRY.definitions()

    assert len(definitions) == len(TOOLS_LIST)
    assert len({definition.spec.name for definition in definitions}) == len(definitions)
    assert all(definition.input_model for definition in definitions)
    assert all(definition.output_model for definition in definitions)
    assert all(
        definition.spec.approval_policy == "required"
        for definition in definitions
        if definition.spec.side_effect == "write"
    )
    assert all(
        definition.spec.approval_policy == "none"
        for definition in definitions
        if definition.spec.side_effect == "read"
    )


def _registry_for(tool_fn, *, side_effect: str = "read", attempts: int = 1) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        tool_fn,
        ToolSpec(
            name=tool_fn.name,
            capability="test",
            side_effect=side_effect,
            approval_policy="required" if side_effect == "write" else "none",
            max_attempts=attempts,
        ),
    )
    return registry


def test_executor_returns_uniform_success_envelope_and_validates_input() -> None:
    @tool
    def lookup(company_name: str) -> dict:
        """Lookup a company."""
        return {"company_name": company_name, "evidence_refs": ["ev-1"]}

    executor = ToolExecutor(_registry_for(lookup))
    outcome = asyncio.run(executor.execute("lookup", {"company_name": "甲公司"}))
    invalid = asyncio.run(executor.execute("lookup", {"company_name": 123}))

    assert outcome.status == "success"
    assert outcome.data["company_name"] == "甲公司"
    assert outcome.evidence_refs == ["ev-1"]
    assert outcome.metrics.attempts == 1
    assert invalid.status == "invalid"
    assert invalid.error is not None
    assert invalid.error.code == "invalid_input"


def test_executor_denies_write_without_approval_and_idempotency() -> None:
    called = False

    @tool
    def write_company(company_name: str) -> dict:
        """Write a company."""
        nonlocal called
        called = True
        return {"success": True}

    executor = ToolExecutor(_registry_for(write_company, side_effect="write"))
    outcome = asyncio.run(executor.execute("write_company", {"company_name": "甲公司"}))

    assert outcome.status == "denied"
    assert outcome.error is not None
    assert outcome.error.code == "approval_required"
    assert called is False


def test_executor_retries_transient_failure_and_records_outcome() -> None:
    attempts = 0
    recorded = []

    @tool
    def unstable(company_name: str) -> dict:
        """Transient test tool."""
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary provider failure")
        return {"company_name": company_name, "status": "partial"}

    executor = ToolExecutor(_registry_for(unstable, attempts=2), call_recorder=recorded.append)
    outcome = asyncio.run(executor.execute("unstable", {"company_name": "甲公司"}))

    assert outcome.status == "partial"
    assert outcome.metrics.attempts == 2
    assert attempts == 2
    assert recorded == [outcome]


def test_executor_rejects_invalid_output_and_write_requires_idempotency_after_approval() -> None:
    @tool
    def invalid_output(company_name: str) -> list:
        """Invalid output test tool."""
        return [company_name]

    invalid = asyncio.run(
        ToolExecutor(_registry_for(invalid_output)).execute(
            "invalid_output", {"company_name": "甲公司"}
        )
    )
    assert invalid.status == "invalid"
    assert invalid.error is not None
    assert invalid.error.code == "invalid_output"

    @tool
    def approved_write(company_name: str) -> dict:
        """Approved write test tool."""
        return {"success": True}

    denied = asyncio.run(
        ToolExecutor(_registry_for(approved_write, side_effect="write")).execute(
            "approved_write",
            {"company_name": "甲公司"},
            ToolContext(approval_token="approval-1"),
        )
    )
    assert denied.status == "invalid"
    assert denied.error is not None
    assert denied.error.code == "idempotency_key_required"


def test_executor_rejects_nonempty_unsigned_write_token() -> None:
    called = False

    @tool
    def unsigned_write(company_name: str) -> dict:
        """Write tool used to verify token authenticity."""
        nonlocal called
        called = True
        return {"success": True, "side_effect_receipt": {"receipt_id": "r-1"}}

    context = ToolContext(
        session_id="session-1",
        run_id="run-1",
        user_id="requester-1",
        approval_token="approval-looks-nonempty",
        approval_proposal_id="proposal-1",
        approval_actor_id="approver-1",
        approval_secret_key="test-secret",
        idempotency_key="idempotency-1",
    )
    outcome = asyncio.run(
        ToolExecutor(_registry_for(unsigned_write, side_effect="write")).execute(
            "unsigned_write", {"company_name": "甲公司"}, context
        )
    )

    assert outcome.status == "denied"
    assert outcome.error is not None
    assert outcome.error.code == "approval_token_invalid"
    assert called is False
