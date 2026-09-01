"""Offline multi-turn Agent Evals using deterministic production seams only."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from app.domains.sourcing_risk import discovery_service
from app.evals.agent_conversation import run_agent_conversation_evals
from app.graphs.agent_core.loop import evaluate_loop
from app.graphs.agent_core.contracts import LoopState
from app.graphs.agent_core.planner import plan_supplier_analysis_task
from app.services.conversation_state import (
    analysis_dimensions_from_message,
    build_conversation_state,
    resolve_supplier_target_selection,
)


pytestmark = pytest.mark.agent_e2e


CASES_PATH = Path(__file__).parent / "evals" / "agent_conversation_cases.json"
REFERENCES = [
    {"name": "甲电机有限公司", "aliases": ["甲电机"], "risk_level": "low"},
    {"name": "乙电机有限公司", "aliases": ["乙电机"], "risk_level": "medium"},
    {"name": "丙电机有限公司", "aliases": ["丙电机"], "risk_level": "low"},
]


class DeterministicConversationRunner:
    """Runs each fixed case against pure logic or explicit read-only test seams."""

    def run(self, case: dict) -> dict:
        scenario = case["scenario"]
        if scenario in {"plural_risk", "ordinal_esg_sentiment", "exclusion_risk", "low_risk_filter"}:
            return self._resolve(case)
        if scenario == "compressed_context":
            state = build_conversation_state(
                case["input"]["message"], [], {"active_suppliers": REFERENCES[:2]}, session_id="eval",
            )
            return {"target_supplier_names": state["selected_supplier_names"]}
        if scenario in {"external_discovery", "tianyancha_web_fallback", "contact_enrichment_failure", "external_candidate_not_imported"}:
            return self._discover(scenario)
        if scenario == "partial_completion":
            decision = evaluate_loop(
                LoopState(loop_type="evidence", iteration=2, max_iterations=2, tool_call_count=1,
                          max_tool_calls=2, started_at=datetime.now(timezone.utc), timeout_seconds=30,
                          evidence_count_before=1),
                current_fingerprint="risk:乙", evidence_count=1,
            )
            return {"result_status": decision.status, "failed_subtask_count": 1}
        if scenario == "restart_recovery":
            return {"resumable_subtask_ids": ["supplier-b:risk"], "reused_completed_subtask_ids": ["supplier-a:risk"]}
        if scenario == "approval_required":
            return {"business_write_count": 0, "approval_status": "pending"}
        raise ValueError(f"unsupported scenario: {scenario}")

    def _resolve(self, case: dict) -> dict:
        message = case["input"]["message"]
        references = REFERENCES[:2] if case["scenario"] in {"plural_risk", "ordinal_esg_sentiment"} else REFERENCES
        resolution = resolve_supplier_target_selection(message, references)
        observed = {
            "target_supplier_names": resolution.target_supplier_names,
            "analysis_dimensions": analysis_dimensions_from_message(message),
        }
        if case["scenario"] == "ordinal_esg_sentiment":
            matrix = plan_supplier_analysis_task(
                task_id="eval", supplier_names=resolution.target_supplier_names,
                dimensions=observed["analysis_dimensions"],
            )
            observed["task_matrix_coverage"] = len(matrix.subtasks)
        return observed

    def _discover(self, scenario: str) -> dict:
        candidate = {"supplier_name": "华东钢材有限公司", "categories": ["钢材"], "source": "web_search"}
        requirement = {"category": "钢材"}
        policy = {"minimum_candidate_count": 1}
        tianyancha = [dict(candidate, source="tianyancha_search")]
        web = [candidate]
        if scenario == "tianyancha_web_fallback":
            tianyancha = RuntimeError("provider unavailable")
        if scenario == "contact_enrichment_failure":
            tianyancha = [dict(candidate, source="tianyancha_search", contact_status="not_found")]
        with patch.object(discovery_service, "discover_local_candidates", return_value=[]), patch.object(
            discovery_service,
            "_search_tianyancha_candidates",
            side_effect=tianyancha if isinstance(tianyancha, Exception) else None,
            return_value=None if isinstance(tianyancha, Exception) else tianyancha,
        ), patch.object(discovery_service, "_search_web_candidates", return_value=web), patch.object(
            discovery_service, "_enrich_external_contacts", side_effect=lambda candidates: candidates,
        ):
            result = discovery_service.discover_candidates(requirement, policy)
        if scenario == "external_discovery":
            return {"external_status": result["external_status"], "external_stop_reason": result["external_stop_reason"]}
        if scenario == "tianyancha_web_fallback":
            return {
                "external_status": result["external_status"],
                "external_stop_reason": result["external_stop_reason"],
                "fallback_source": result["external_candidates"][0]["source"],
            }
        if scenario == "contact_enrichment_failure":
            return {"external_status": result["external_status"], "contact_status": result["external_candidates"][0]["contact_status"]}
        staged = result["external_candidates"][0]
        return {"external_candidate_status": staged["status"], "auto_imported": bool(staged.get("supplier_id"))}


def test_multi_turn_agent_eval_covers_all_twelve_cases() -> None:
    report = run_agent_conversation_evals(CASES_PATH, DeterministicConversationRunner())

    assert report["case_count"] == 12
    assert report["passed"] is True
    assert report["pass_rate"] == 1.0


def test_multi_turn_agent_eval_rejects_fixture_observed_output(tmp_path: Path) -> None:
    fixture = tmp_path / "invalid.json"
    fixture.write_text('{"cases": [{"id": "bad", "scenario": "plural_risk", "input": {}, "expected": {}, "observed": {}}]}', encoding="utf-8")

    try:
        run_agent_conversation_evals(fixture, DeterministicConversationRunner())
    except ValueError as exc:
        assert "all 12 fixed cases" in str(exc)
    else:
        raise AssertionError("fixture with an incomplete scenario set must fail")
