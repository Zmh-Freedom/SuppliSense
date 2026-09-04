"""Task 14 regressions for the sourcing workflow on the active Harness path."""

from __future__ import annotations

import asyncio

import pytest

from app.domains.sourcing_risk import discovery_service
from app.domains.sourcing_risk.requirement_service import resolve_harness_requirement
from app.graphs.agent_core.adapter import _apply_harness_sourcing_requirement
from app.graphs.harness.graph import _build_default_plan
from app.tools import TOOL_REGISTRY
from app.tools.executor import ToolContext, ToolExecutor


pytestmark = pytest.mark.agent_e2e


def test_harness_requirement_extracts_broad_category_without_inventing_constraints(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing_risk.requirement_service.settings.LLM_API_KEY", "")

    result = resolve_harness_requirement("给我推荐工业相机的供应商")

    assert result["status"] == "ready"
    assert result["extraction_source"] == "deterministic_fallback"
    assert result["requirement"]["category"] == "工业相机"
    assert result["requirement"]["product"] == "工业相机"
    assert result["requirement"]["must_have"] == []


def test_harness_binds_requirement_to_current_task_before_planning(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing_risk.requirement_service.settings.LLM_API_KEY", "")
    context = {
        "references": [],
        "conversation_state": {},
        "current_task": {"task_id": "source-1", "task_type": "sourcing"},
    }

    resolved = _apply_harness_sourcing_requirement(context, "推荐钢材供应商")

    task = resolved["current_task"]
    assert task["requirement_status"] == "ready"
    assert task["requirement"]["category"] == "钢材"
    planned = _build_default_plan({"current_task": task, "execution_context": resolved})
    assert planned[0].tool_name == "discover_supplier_candidates"
    assert planned[0].arguments["requirement"]["category"] == "钢材"


def test_harness_sourcing_keeps_formal_candidates_when_external_stage_fails(monkeypatch) -> None:
    formal = {
        "supplier_id": "supplier-1",
        "supplier_name": "华东钢材有限公司",
        "categories": ["钢材"],
        "source": "feishu_bitable",
        "source_type": "feishu_bitable",
        "source_stage": "feishu_formal",
        "source_reference": "supplier-record-1",
        "website_url": "https://steel.example.test",
    }
    monkeypatch.setattr(
        discovery_service,
        "discover_candidates",
        lambda *_args: {
            "source": "local",
            "source_order": ["local_history", "feishu_formal", "tianyancha", "web_search"],
            "local_candidates": [formal],
            "local_status": "ok",
            "local_failure_reason": None,
            "external_candidates": [],
            "external_status": "failed",
            "external_stop_reason": "external_sources_failed",
            "external_failure_reasons": [{"stage": "tianyancha", "reason": "阶段调用失败"}],
            "external_loop": {"iterations": 1, "max_iterations": 3},
        },
    )

    outcome = asyncio.run(
        ToolExecutor(TOOL_REGISTRY).execute(
            "discover_supplier_candidates",
            {"requirement": {"category": "钢材", "specification": "钢材"}},
            ToolContext(session_id="s", run_id="r"),
        )
    )

    assert outcome.status == "success"
    assert outcome.data["candidates"][0]["candidate_type"] == "formal"
    assert outcome.data["candidates"][0]["identity_status"] == "exact"
    assert outcome.data["external_status"] == "failed"
    assert outcome.data["external_failure_reasons"]
    assert outcome.data["claims"]
    assert outcome.data["evidence_records"]


def test_harness_sourcing_exposes_external_candidate_as_pending_verification(monkeypatch) -> None:
    external = {
        "candidate_id": "candidate-1",
        "supplier_name": "公开钢材候选有限公司",
        "source": "web_search",
        "source_type": "public_web_search",
        "source_stage": "external",
        "source_reference": "https://example.test/supplier",
        "identity_status": "unavailable",
    }
    monkeypatch.setattr(
        discovery_service,
        "discover_candidates",
        lambda *_args: {
            "source": "local_and_external",
            "source_order": ["local_history", "feishu_formal", "tianyancha", "web_search"],
            "local_candidates": [],
            "local_status": "ok",
            "external_candidates": [external],
            "external_status": "partial",
            "external_stop_reason": "external_sources_exhausted",
            "external_failure_reasons": [],
            "external_loop": {"iterations": 3, "max_iterations": 3},
        },
    )

    outcome = asyncio.run(
        ToolExecutor(TOOL_REGISTRY).execute(
            "discover_supplier_candidates",
            {"requirement": {"category": "钢材"}},
            ToolContext(session_id="s", run_id="r"),
        )
    )

    candidate = outcome.data["external_candidates"][0]
    assert outcome.status == "success"
    assert candidate["candidate_type"] == "external"
    assert candidate["identity_status"] == "unavailable"
    assert candidate["verification_status"] == "pending_verification"
    assert outcome.data["source_order"][-2:] == ["tianyancha", "web_search"]
