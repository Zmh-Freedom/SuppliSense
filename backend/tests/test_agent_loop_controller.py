"""Tests for the bounded Agent loop controller."""

from datetime import datetime, timedelta, timezone

from app.graphs.agent_core.contracts import LoopState
from app.graphs.agent_core.loop import evaluate_loop


def make_loop_state(**overrides: object) -> LoopState:
    values: dict[str, object] = {
        "loop_type": "evidence",
        "iteration": 0,
        "max_iterations": 2,
        "tool_call_count": 0,
        "max_tool_calls": 3,
        "started_at": datetime.now(timezone.utc),
        "timeout_seconds": 30,
        "evidence_count_before": 0,
    }
    values.update(overrides)
    return LoopState(**values)


def test_evaluate_loop_blocks_when_tool_budget_is_exhausted_without_evidence():
    decision = evaluate_loop(
        make_loop_state(tool_call_count=3),
        current_fingerprint="search:camera",
        evidence_count=0,
    )

    assert decision.status == "blocked"
    assert decision.stop_reason == "tool_budget_exhausted"


def test_evaluate_loop_blocks_repeated_fingerprint_without_new_evidence():
    decision = evaluate_loop(
        make_loop_state(previous_fingerprint="search:camera"),
        current_fingerprint="search:camera",
        evidence_count=0,
    )

    assert decision.status == "blocked"
    assert decision.stop_reason == "repeated_fingerprint_without_new_evidence"


def test_evaluate_loop_requests_review_when_no_new_evidence_is_found():
    decision = evaluate_loop(
        make_loop_state(evidence_count_before=2),
        current_fingerprint="search:camera-page-2",
        evidence_count=2,
    )

    assert decision.status == "needs_review"
    assert decision.stop_reason == "no_new_evidence"


def test_evaluate_loop_returns_partial_when_timeout_keeps_existing_evidence():
    state = make_loop_state(evidence_count_before=1, timeout_seconds=30)
    decision = evaluate_loop(
        state,
        current_fingerprint="search:camera",
        evidence_count=1,
        now=state.started_at + timedelta(seconds=30),
    )

    assert decision.status == "partial"
    assert decision.stop_reason == "timeout"


def test_evaluate_loop_continues_within_all_budgets_when_evidence_increases():
    decision = evaluate_loop(
        make_loop_state(iteration=1, tool_call_count=1, evidence_count_before=1),
        current_fingerprint="search:camera-page-2",
        evidence_count=2,
    )

    assert decision.status == "continue"
    assert decision.stop_reason == "within_budget"
