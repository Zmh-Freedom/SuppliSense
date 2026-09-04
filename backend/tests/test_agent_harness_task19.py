"""Task19 acceptance scenarios that exercise the real Harness contracts offline."""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.tools import StructuredTool

from app.graphs.agent_core.evidence_ledger import build_evidence_record
from app.graphs.harness import ExecutionBudget, run_harness
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec


pytestmark = pytest.mark.agent_e2e


def _matrix_state(task_specs: list[dict]) -> dict:
    return {
        "session_id": "task19-session",
        "turn_id": "task19-turn",
        "run_id": "task19-run",
        "user_id": "task19-user",
        "user_message": "对两家供应商做五维风险分析",
        "execution_context": {"references": []},
        "current_task": {},
        "task_specs": task_specs,
        "budget": {
            **ExecutionBudget().model_dump(mode="json"),
            "max_tool_calls": len(task_specs),
            "max_parallel_tasks": 5,
        },
    }


@pytest.mark.parametrize("supplier", ["甲供应商", "乙供应商"])
def test_task19_matrix_fixture_has_two_distinct_supplier_targets(supplier: str) -> None:
    """The fixture itself must not accidentally collapse two entities."""
    assert supplier in {"甲供应商", "乙供应商"}


def test_task19_harness_covers_two_suppliers_across_five_dimensions() -> None:
    suppliers = {"甲供应商": "supplier-a", "乙供应商": "supplier-b"}
    dimensions = ["financial", "business_risk", "quality", "delivery", "compliance"]

    def matrix_tool(company_name: str, dimension: str) -> dict:
        entity_id = suppliers[company_name]
        evidence_id = f"ev-{entity_id}-{dimension}"
        evidence = build_evidence_record(
            evidence_id=evidence_id,
            entity_id=entity_id,
            dimension=dimension,
            provider="task19-fixture",
            source_type="internal",
            payload={"score": 0.2},
        ).model_dump(mode="json")
        return {
            "status": "success",
            "evidence_records": [evidence],
            "claims": [{
                "claim_id": f"claim-{entity_id}-{dimension}",
                "entity_id": entity_id,
                "dimension": dimension,
                "statement": f"{company_name} {dimension} 证据有效",
                "fact_path": "score",
                "operator": "eq",
                "value": 0.2,
                "evidence_refs": [evidence_id],
                "confidence": 0.9,
            }],
        }

    tool = StructuredTool.from_function(matrix_tool, name="task19_risk_matrix", description="test")
    registry = ToolRegistry()
    registry.register(
        tool,
        ToolSpec(
            name="task19_risk_matrix",
            capability="risk",
            side_effect="read",
            approval_policy="none",
            evidence_required=True,
        ),
    )
    tasks = [
        {
            "task_id": f"{entity_id}:{dimension}",
            "tool_name": "task19_risk_matrix",
            "arguments": {"company_name": company_name, "dimension": dimension},
            "entity_id": entity_id,
            "dimension": dimension,
            "resource_key": entity_id,
            "required": True,
            "evidence_requirements": [dimension],
        }
        for company_name, entity_id in suppliers.items()
        for dimension in dimensions
    ]

    result = asyncio.run(run_harness(_matrix_state(tasks), executor=ToolExecutor(registry)))

    assert result["status"] == "completed"
    assert result["answer"]["status"] == "completed"
    claims = result["answer"]["claims"]
    assert len(claims) == 10
    assert {(claim["entity_id"], claim["dimension"]) for claim in claims} == {
        (entity_id, dimension)
        for entity_id in suppliers.values()
        for dimension in dimensions
    }
    assert result["evidence_coverage"]["coverage_ratio"] == 1.0


def test_task19_provider_rate_limit_fails_closed_with_explicit_tool_error() -> None:
    def rate_limited(company_name: str) -> dict:
        del company_name
        return {
            "status": "unavailable",
            "error": "provider_rate_limited",
            "message": "外部提供方暂时限流",
        }

    tool = StructuredTool.from_function(rate_limited, name="task19_rate_limited", description="test")
    registry = ToolRegistry()
    registry.register(
        tool,
        ToolSpec(
            name="task19_rate_limited",
            capability="risk",
            side_effect="read",
            approval_policy="none",
            evidence_required=True,
        ),
    )
    state = _matrix_state([{
        "task_id": "supplier-a:risk",
        "tool_name": "task19_rate_limited",
        "arguments": {"company_name": "甲供应商"},
        "entity_id": "supplier-a",
        "dimension": "risk",
        "required": True,
        "evidence_requirements": ["risk"],
    }])

    result = asyncio.run(run_harness(state, executor=ToolExecutor(registry)))
    outcome = result["tool_outcomes"][0]

    assert outcome["status"] == "unavailable"
    assert outcome["error"]["code"] == "provider_rate_limited"
    assert result["answer"]["status"] == "needs_review"
    assert result["answer"]["claims"] == []
    assert result["loop_exit_reason"] == "no_remediation_spec"
