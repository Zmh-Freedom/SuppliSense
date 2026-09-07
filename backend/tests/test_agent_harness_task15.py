"""Task 15 regressions for the five-dimension risk Harness path."""

from __future__ import annotations

import asyncio

from langchain_core.tools import StructuredTool

from app.domains.risk.operational_risk_service import assess_operational_risk
from app.domains.risk.risk_contract import RiskAssessmentRequest, get_risk_dimension_spec
from app.graphs.agent_core.planner import plan_supplier_analysis_task
from app.graphs.agent_core.evidence_ledger import Claim, EvidenceLedger, EvidenceRecord
from app.graphs.harness import run_harness
from app.services.conversation_state import analysis_dimensions_from_message
from app.tools.executor import ToolContext, ToolExecutor
from app.tools.evidence import attach_tool_evidence
from app.tools.registry import ToolRegistry, ToolSpec


def test_risk_request_and_capability_matrix_cover_five_dimensions() -> None:
    request = RiskAssessmentRequest(
        suppliers=["甲公司", "甲公司", "乙公司"],
        dimensions=["risk", "financial", "business_risk", "quality", "delivery", "compliance"],
    )

    assert request.suppliers == ["甲公司", "乙公司"]
    assert get_risk_dimension_spec("quality").tool_name == "assess_operational_risk"
    assert get_risk_dimension_spec("delivery").time_window == "latest_monthly_snapshot"


def test_conversation_state_extracts_five_dimensions() -> None:
    assert analysis_dimensions_from_message("对这家公司做五维风险分析") == [
        "risk", "financial", "business_risk", "quality", "delivery", "compliance",
    ]


def test_conversation_state_defaults_generic_review_to_available_real_dimensions() -> None:
    assert analysis_dimensions_from_message("复核青岛三祥科技股份有限公司") == [
        "risk", "financial", "business_risk",
    ]
    assert analysis_dimensions_from_message("财务复核青岛三祥科技股份有限公司") == [
        "financial",
    ]


def test_planner_builds_supplier_dimension_matrix_with_required_evidence() -> None:
    task = plan_supplier_analysis_task(
        task_id="risk-15",
        supplier_names=["甲公司", "乙公司"],
        dimensions=["risk", "financial", "business_risk", "quality", "delivery", "compliance"],
    )

    assert len(task.subtasks) == 12
    assert len({(item.supplier_name, item.dimension) for item in task.subtasks}) == 12
    assert all(item.required for item in task.subtasks)
    assert all(item.evidence_requirements == [item.dimension] for item in task.subtasks)


class _Collection:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def find_one(self, query: dict) -> dict | None:
        return next(
            (document for document in self.documents if all(document.get(key) == value for key, value in query.items())),
            None,
        )

    def find(self, query: dict) -> list[dict]:
        return [
            document for document in self.documents
            if all(document.get(key) == value for key, value in query.items())
        ]


class _Database(dict):
    def __getitem__(self, name: str) -> _Collection:
        return super().__getitem__(name)


def test_operational_risk_missing_fields_is_not_low_risk(monkeypatch) -> None:
    database = _Database({
        "supplier_master_snapshots": _Collection([{
            "supplier_code": "S-1", "name": "甲公司", "sync_status": "current",
        }]),
        "supplier_transaction_snapshots": _Collection([{
            "supplier_code": "S-1", "sync_status": "current", "data_mode": "real",
            "eligible_for_formal_assessment": True, "snapshot_month": "2026-08",
            "received_qty": 100, "received_amount": 1000,
        }]),
    })
    monkeypatch.setattr("app.domains.risk.operational_risk_service.get_db", lambda: database)

    result = assess_operational_risk("甲公司", "quality")

    assert result["assessment_status"] == "missing_data"
    assert result["risk_score"] is None
    assert result["risk_level"] is None
    assert result["data_coverage"]["coverage_ratio"] == 0.0


def test_formal_real_source_is_normalized_to_formal_evidence_mode() -> None:
    result = attach_tool_evidence(
        {
            "assessment_status": "partial",
            "supplier_reference": "S-1",
            "evidence": [{
                "source": "feishu_transaction_snapshot",
                "data_mode": "real",
                "facts": {"risk_level": "medium"},
            }],
        },
        tool_name="assess_business_risk",
        entity_id="entity:S-1",
        dimension="business_risk",
        claim_fields=["risk_level"],
    )

    assert result["evidence_records"][0]["data_mode"] == "formal"
    assert result["claims"][0]["fact_path"] == "risk_level"


def test_tool_evidence_is_complete_and_claim_is_supported() -> None:
    result = attach_tool_evidence(
        {"risk_score": 12, "risk_level": "低风险"},
        tool_name="assess_risk",
        entity_id="entity:甲公司",
        dimension="risk",
        source_type="risk_service_result",
        claim_fields=["risk_score", "risk_level"],
    )

    record = EvidenceRecord.model_validate(result["evidence_records"][0])
    claims = [Claim.model_validate(item) for item in result["claims"]]
    validation = [EvidenceLedger([record]).validate_claim(claim) for claim in claims]

    assert record.content_hash
    assert all(item.claim.validation_status == "supported" for item in validation)


def test_harness_entity_id_is_used_for_domain_tool_evidence() -> None:
    def risk_tool(company_name: str) -> dict:
        return attach_tool_evidence(
            {"company_name": company_name, "risk_score": 12, "risk_level": "低风险"},
            tool_name="assess_risk",
            entity_id=f"entity:{company_name}",
            dimension="risk",
            claim_fields=["risk_score", "risk_level"],
        )

    tool = StructuredTool.from_function(risk_tool, name="assess_risk", description="test")
    registry = ToolRegistry()
    registry.register(
        tool,
        ToolSpec(
            name="assess_risk",
            capability="risk",
            side_effect="read",
            approval_policy="none",
            evidence_required=True,
        ),
    )

    outcome = asyncio.run(ToolExecutor(registry).execute(
        "assess_risk",
        {"company_name": "甲公司"},
        ToolContext(entity_id="supplier:formal:甲公司"),
    ))

    assert outcome.status == "success"
    assert outcome.data["evidence_records"][0]["entity_id"] == "supplier:formal:甲公司"
    assert all(
        claim["entity_id"] == "supplier:formal:甲公司"
        for claim in outcome.data["claims"]
    )


def test_harness_maps_quality_and_delivery_to_independent_tasks() -> None:
    def operational(company_name: str, dimension: str) -> dict:
        evidence_id = f"ev:{company_name}:{dimension}"
        return {
            "status": "success",
            "evidence_records": [{
                "evidence_id": evidence_id,
                "entity_id": f"entity:{company_name}",
                "dimension": dimension,
                "provider": "test",
                "source_type": "test",
                "status": "available",
                "collected_at": "2026-09-04T00:00:00+00:00",
                "data_mode": "formal",
                "content_hash": evidence_id,
                "facts": {"risk_level": "low"},
            }],
            "claims": [{
                "claim_id": f"claim:{company_name}:{dimension}",
                "entity_id": f"entity:{company_name}",
                "dimension": dimension,
                "statement": f"{company_name} {dimension} risk_level is low",
                "value": "low",
                "fact_path": "risk_level",
                "evidence_refs": [evidence_id],
                "confidence": 0.9,
            }],
        }

    tool = StructuredTool.from_function(operational, name="assess_operational_risk", description="test")
    registry = ToolRegistry()
    registry.register(
        tool,
        ToolSpec(name="assess_operational_risk", capability="operational_risk", side_effect="read", approval_policy="none"),
    )
    result = asyncio.run(run_harness({
        "session_id": "s-15", "turn_id": "t-15", "run_id": "r-15",
        "user_message": "做质量和交付风险分析", "execution_context": {"references": []},
        "current_task": {
            "task_id": "risk-15", "task_type": "analysis",
            "target_supplier_names": ["甲公司"],
            "analysis_dimensions": ["quality", "delivery"],
        },
        "budget": {"max_tool_calls": 4},
    }, executor=ToolExecutor(registry)))

    assert [item["dimension"] for item in result["task_specs"]] == ["quality", "delivery"]
    assert result["tool_call_count"] == 2
    assert result["answer"]["status"] == "completed"
    assert len(result["answer"]["claims"]) == 2
