"""Task 12 production tool contract and evidence-gate regressions."""

import asyncio

from langchain_core.tools import tool
from pydantic import BaseModel

from app.tools import TOOLS_LIST, TOOL_REGISTRY
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec


def test_default_tools_use_named_strict_output_contracts() -> None:
    definitions = TOOL_REGISTRY.definitions()

    assert len(definitions) == len(TOOLS_LIST) == 30
    assert all(definition.output_model.__name__ != "ToolPayload" for definition in definitions)
    assert all(definition.output_model.model_config.get("extra") == "forbid" for definition in definitions)


def _registry_for(tool_fn, *, evidence_required: bool = False) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        tool_fn,
        ToolSpec(
            name=tool_fn.name,
            capability="test",
            side_effect="read",
            approval_policy="none",
            evidence_required=evidence_required,
        ),
    )
    return registry


def test_evidence_required_rejects_success_without_evidence() -> None:
    @tool
    def risk_lookup(company_name: str) -> dict:
        """Return an untraceable result."""
        return {"company_name": company_name, "risk_level": "低风险"}

    outcome = asyncio.run(
        ToolExecutor(_registry_for(risk_lookup, evidence_required=True)).execute(
            "risk_lookup", {"company_name": "甲公司"}
        )
    )

    assert outcome.status == "invalid"
    assert outcome.error is not None
    assert outcome.error.code == "evidence_required"


def test_evidence_required_accepts_explicit_reference() -> None:
    @tool
    def risk_lookup(company_name: str) -> dict:
        """Return a traceable result."""
        return {
            "company_name": company_name,
            "risk_level": "低风险",
            "evidence_refs": ["risk:甲公司:2026Q3"],
        }

    outcome = asyncio.run(
        ToolExecutor(_registry_for(risk_lookup, evidence_required=True)).execute(
            "risk_lookup", {"company_name": "甲公司"}
        )
    )

    assert outcome.status == "success"
    assert outcome.evidence_refs == ["risk:甲公司:2026Q3"]


def test_evidence_required_rejects_incomplete_evidence_metadata() -> None:
    @tool
    def risk_lookup(company_name: str) -> dict:
        """Return an incomplete evidence record."""
        return {
            "company_name": company_name,
            "risk_level": "低风险",
            "evidence_records": [{"evidence_id": "ev-1", "status": "available"}],
        }

    outcome = asyncio.run(
        ToolExecutor(_registry_for(risk_lookup, evidence_required=True)).execute(
            "risk_lookup", {"company_name": "甲公司"}
        )
    )

    assert outcome.status == "invalid"
    assert outcome.error is not None
    assert outcome.error.code == "evidence_invalid"


def test_empty_output_is_invalid_even_without_evidence_requirement() -> None:
    @tool
    def empty_lookup(company_name: str) -> dict:
        """Return an invalid empty payload."""
        del company_name
        return {}

    outcome = asyncio.run(
        ToolExecutor(_registry_for(empty_lookup)).execute(
            "empty_lookup", {"company_name": "甲公司"}
        )
    )

    assert outcome.status == "invalid"
    assert outcome.error is not None
    assert outcome.error.code == "invalid_output"


def test_strict_contract_rejects_unknown_top_level_field() -> None:
    class Output(BaseModel):
        model_config = {"extra": "forbid"}

        value: str

    @tool
    def strict_lookup(company_name: str) -> dict:
        """Return an unknown field."""
        del company_name
        return {"value": "ok", "unexpected": True}

    registry = ToolRegistry()
    registry.register(
        strict_lookup,
        ToolSpec(name="strict_lookup", capability="test", side_effect="read", approval_policy="none"),
        output_model=Output,
    )
    outcome = asyncio.run(
        ToolExecutor(registry).execute("strict_lookup", {"company_name": "甲公司"})
    )

    assert outcome.status == "invalid"
    assert outcome.error is not None
    assert outcome.error.code == "invalid_output"
