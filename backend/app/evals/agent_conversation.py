"""Deterministic, service-free Eval contracts for multi-turn Agent behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


_REQUIRED_SCENARIOS = frozenset({
    "plural_risk", "ordinal_esg_sentiment", "exclusion_risk", "low_risk_filter",
    "external_discovery", "tianyancha_web_fallback", "contact_enrichment_failure",
    "external_candidate_not_imported", "partial_completion", "compressed_context",
    "restart_recovery", "approval_required",
})


class AgentConversationEvalRunner(Protocol):
    """Executes one case and returns facts observed from the real code path."""

    def run(self, case: dict[str, Any]) -> dict[str, Any]: ...


def load_agent_conversation_cases(cases_path: str | Path) -> list[dict[str, Any]]:
    """Load and validate the fixed multi-turn Eval fixture without executing services."""
    payload = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list) or len(cases) != len(_REQUIRED_SCENARIOS):
        raise ValueError("Agent conversation Eval must define all 12 fixed cases")
    scenarios = [case.get("scenario") for case in cases if isinstance(case, dict)]
    if set(scenarios) != _REQUIRED_SCENARIOS or len(set(scenarios)) != len(scenarios):
        raise ValueError("Agent conversation Eval scenarios must be complete and unique")
    for case in cases:
        if not isinstance(case.get("id"), str) or not case["id"]:
            raise ValueError("Agent conversation Eval case id is required")
        if not isinstance(case.get("input"), dict) or not isinstance(case.get("expected"), dict):
            raise ValueError(f"Agent conversation Eval case {case['id']} requires input and expected")
        if "observed" in case:
            raise ValueError(f"Agent conversation Eval case {case['id']} must not embed observed output")
    return cases


def evaluate_agent_conversation_case(case: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    """Compare one observed execution with its fixed, explicit expectations."""
    expected = case.get("expected")
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        raise ValueError("case expectation and observed execution must be objects")
    mismatches = {
        field: {"expected": value, "actual": observed.get(field)}
        for field, value in expected.items()
        if observed.get(field) != value
    }
    return {
        "id": case["id"],
        "scenario": case["scenario"],
        "passed": not mismatches,
        "mismatches": mismatches,
    }


def run_agent_conversation_evals(
    cases_path: str | Path,
    runner: AgentConversationEvalRunner,
) -> dict[str, Any]:
    """Execute the fixed Eval suite through an injected deterministic runner."""
    cases = load_agent_conversation_cases(cases_path)
    results = [evaluate_agent_conversation_case(case, runner.run(case)) for case in cases]
    return {
        "eval_version": "agent-conversation-v1",
        "case_count": len(results),
        "passed": all(result["passed"] for result in results),
        "pass_rate": sum(result["passed"] for result in results) / len(results),
        "cases": results,
    }
