"""Behavior tests for the durable Sourcing Risk V2 orchestration graph."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graphs.sourcing_risk_v2 import nodes
from app.graphs.sourcing_risk_v2.checkpointer import settings
from app.graphs.sourcing_risk_v2.runner import build_sourcing_risk_graph


def _requirement() -> dict:
    return {"category": "摄像头", "specification": "IP67", "candidate_count": 1}


def _policy() -> dict:
    return {
        "weights": {
            "match": 0.25,
            "capacity": 0.20,
            "performance": 0.15,
            "quality": 0.15,
            "risk": 0.15,
            "commercial": 0.10,
        },
        "missing_penalties": {key: 0.0 for key in ("match", "capacity", "performance", "quality", "risk", "commercial")},
        "stale_penalties": {key: 0.0 for key in ("match", "capacity", "performance", "quality", "risk", "commercial")},
        "required_evidence": ["sanctions"],
        "hard_gates": {
            "unmatched_category": "rejected",
            "supplier_not_active": "rejected",
            "mandatory_qualification_missing": "rejected",
            "identity_unverified": "needs_review",
            "sanctions_hit": "rejected",
            "sanctions_available": "needs_review",
            "key_evidence_conflict": "needs_review",
        },
        "freshness_days": {"sanctions": 30},
    }


def _candidate() -> dict:
    company_id = str(uuid4())
    return {
        "supplier_name": "示例供应商",
        "company_id": company_id,
        "identity_company_id": company_id,
        "active": True,
        "categories": ["摄像头"],
        "specifications": ["IP67"],
        "dimension_scores": {
            "match": 100,
            "capacity": 80,
            "performance": 80,
            "quality": 80,
            "risk": 80,
            "commercial": 80,
        },
    }


def _state_with_candidate() -> dict:
    return {"run_id": str(uuid4()), "requirement_input": _requirement(), "candidates": [_candidate()]}


@pytest.fixture(autouse=True)
def enable_v2_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(nodes, "append_typed_event", lambda *_args, **_kwargs: None)


def test_ambiguous_identity_interrupts_before_investigation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipping the interrupt would investigate an identity a reviewer has not confirmed."""
    investigated = False

    monkeypatch.setattr(nodes, "parse_requirement", lambda *_: {"status": "ready", "requirement": _requirement()})
    policy = _policy() | {"checksum": "policy-id"}
    monkeypatch.setattr(nodes, "freeze_policy_snapshot", lambda *_: policy)
    monkeypatch.setattr(nodes, "discover_local_candidates", lambda *_: [_candidate()])
    monkeypatch.setattr(nodes, "is_candidate_supply_sufficient", lambda *_: True)
    monkeypatch.setattr(nodes, "resolve_candidate_identity", lambda _: {"identity_status": "candidates", "identity_candidates": [{"company_id": "a"}], "score_eligible": False})

    async def investigate(*_args, **_kwargs):
        nonlocal investigated
        investigated = True
        return {}

    monkeypatch.setattr(nodes, "investigate_candidates", investigate)
    graph = build_sourcing_risk_graph(InMemorySaver())

    result = asyncio.run(graph.ainvoke(_state_with_candidate(), {"configurable": {"thread_id": "run-id"}}))

    assert result["status"] == "IDENTITY_REVIEW"
    assert result["next_action"] == "identity_review_required"
    assert result["pending_review_ids"]
    assert investigated is False


def test_resume_after_identity_review_continues_from_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing the persistent interrupt resume path would strand a reviewed run."""
    candidate = _candidate()
    monkeypatch.setattr(nodes, "parse_requirement", lambda *_: {"status": "ready", "requirement": _requirement()})
    policy = _policy() | {"checksum": "policy-id"}
    monkeypatch.setattr(nodes, "freeze_policy_snapshot", lambda *_: policy)
    monkeypatch.setattr(nodes, "discover_local_candidates", lambda *_: [candidate])
    monkeypatch.setattr(nodes, "is_candidate_supply_sufficient", lambda *_: True)
    monkeypatch.setattr(nodes, "resolve_candidate_identity", lambda _: {"identity_status": "candidates", "identity_candidates": [{"company_id": candidate["company_id"]}], "score_eligible": False})
    async def investigate(*_args):
        return ({candidate["company_id"]: [{"dimension": "sanctions", "claim_code": "clear", "freshness_status": "fresh", "conflict_status": "none", "evidence_id": "s-1"}]}, [])

    monkeypatch.setattr(nodes, "investigate_candidates", investigate)
    monkeypatch.setattr(nodes, "validate_evidence_set", lambda *_: {"status": "clear", "reason_codes": [], "score_eligible": True})
    monkeypatch.setattr(nodes, "decide_candidates", lambda *_: [{"company_id": candidate["company_id"], "group": "recommended", "final_score": 88.0}])
    graph = build_sourcing_risk_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "resume-run"}}

    paused = asyncio.run(graph.ainvoke({"run_id": str(uuid4()), "requirement_input": _requirement(), "candidates": [candidate]}, config))
    resumed = asyncio.run(graph.ainvoke(Command(resume={"identity_resolutions": {candidate["company_id"]: candidate["company_id"]}}), config))

    assert paused["status"] == "IDENTITY_REVIEW"
    assert resumed["status"] == "READY_FOR_REVIEW"
    assert resumed["decisions"] == [{"company_id": candidate["company_id"], "group": "recommended", "final_score": 88.0}]


def test_sanctions_timeout_retries_once_and_closes_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treating a sanctions timeout as clear would score an unverified supplier."""
    candidate = _candidate()
    attempts = 0

    async def sanctions(_candidate: dict) -> dict:
        nonlocal attempts
        attempts += 1
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(nodes, "fetch_sanctions", sanctions)
    monkeypatch.setattr(nodes, "_sleep", lambda *_: None)

    evidence, failures = asyncio.run(nodes.investigate_candidate(candidate))

    assert attempts == 2
    assert failures == ["sanctions"]
    assert [item for item in evidence if item["dimension"] == "sanctions"] == [{"dimension": "sanctions", "claim_code": "unavailable", "freshness_status": "unknown", "conflict_status": "unknown", "source_type": "provider_error"}]


def test_noncritical_provider_failure_preserves_successful_evidence_as_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dropping successful results after one noncritical failure would erase useful evidence."""
    candidate = _candidate()

    async def financial(_candidate: dict) -> dict:
        raise ConnectionError("financial unavailable")

    async def sanctions(_candidate: dict) -> dict:
        return {"claim_code": "clear", "freshness_status": "fresh", "conflict_status": "none"}

    monkeypatch.setattr(nodes, "fetch_financial", financial)
    monkeypatch.setattr(nodes, "fetch_sanctions", sanctions)
    monkeypatch.setattr(nodes, "_sleep", lambda *_: None)

    evidence, failures = asyncio.run(nodes.investigate_candidate(candidate))

    assert "financial" in failures
    assert "sanctions" not in failures
    assert any(item["dimension"] == "sanctions" and item["claim_code"] == "clear" for item in evidence)


def test_clarification_stops_before_policy_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Continuing after an incomplete requirement would freeze a policy for unknown scope."""
    policy_locked = False
    monkeypatch.setattr(nodes, "parse_requirement", lambda *_: {"status": "clarification_required", "missing": ["specification"]})

    def freeze(*_args):
        nonlocal policy_locked
        policy_locked = True
        return _policy()

    monkeypatch.setattr(nodes, "freeze_policy_snapshot", freeze)
    graph = build_sourcing_risk_graph(InMemorySaver())

    result = asyncio.run(graph.ainvoke({"run_id": str(uuid4()), "requirement_input": {"requirement_text": "找摄像头供应商"}}, {"configurable": {"thread_id": "clarify-run"}}))

    assert result["status"] == "CLARIFYING"
    assert result["next_action"] == "clarification_required"
    assert policy_locked is False


def test_complete_local_flow_reaches_review_without_external_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routing sufficient local candidates to external discovery would add unneeded provider risk."""
    candidate = _candidate()
    external_called = False
    policy = _policy() | {"checksum": "policy-id"}
    monkeypatch.setattr(nodes, "parse_requirement", lambda *_: {"status": "ready", "requirement": _requirement()})
    monkeypatch.setattr(nodes, "freeze_policy_snapshot", lambda *_: policy)
    monkeypatch.setattr(nodes, "discover_local_candidates", lambda *_: [candidate])
    monkeypatch.setattr(nodes, "is_candidate_supply_sufficient", lambda *_: True)
    monkeypatch.setattr(nodes, "resolve_candidate_identity", lambda _: {"identity_status": "exact", "company_id": candidate["company_id"], "score_eligible": True})

    async def external(*_args):
        nonlocal external_called
        external_called = True
        return []

    async def investigate(*_args):
        return ({candidate["company_id"]: [{"dimension": "sanctions", "claim_code": "clear", "freshness_status": "fresh", "conflict_status": "none", "evidence_id": "s-1"}]}, [])

    monkeypatch.setattr(nodes, "search_external_provider", external)
    monkeypatch.setattr(nodes, "investigate_candidates", investigate)
    monkeypatch.setattr(nodes, "validate_evidence_set", lambda *_: {"status": "clear", "reason_codes": [], "score_eligible": True})
    monkeypatch.setattr(nodes, "decide_candidates", lambda *_: [{"company_id": candidate["company_id"], "group": "recommended", "final_score": 88.0}])
    graph = build_sourcing_risk_graph(InMemorySaver())

    result = asyncio.run(graph.ainvoke({"run_id": str(uuid4()), "requirement_input": _requirement()}, {"configurable": {"thread_id": "local-run"}}))

    assert result["status"] == "READY_FOR_REVIEW"
    assert result["decisions"][0]["group"] == "recommended"
    assert external_called is False


def test_conflicting_sanctions_routes_to_needs_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scoring contradictory sanctions evidence would hide a material conflict from reviewers."""
    candidate = _candidate()
    policy = _policy() | {"checksum": "policy-id"}
    monkeypatch.setattr(nodes, "parse_requirement", lambda *_: {"status": "ready", "requirement": _requirement()})
    monkeypatch.setattr(nodes, "freeze_policy_snapshot", lambda *_: policy)
    monkeypatch.setattr(nodes, "discover_local_candidates", lambda *_: [candidate])
    monkeypatch.setattr(nodes, "is_candidate_supply_sufficient", lambda *_: True)
    monkeypatch.setattr(nodes, "resolve_candidate_identity", lambda _: {"identity_status": "exact", "company_id": candidate["company_id"], "score_eligible": True})

    async def investigate(*_args):
        return ({candidate["company_id"]: [{"dimension": "sanctions", "claim_code": "clear", "freshness_status": "fresh", "conflict_status": "conflicting", "evidence_id": "s-1"}]}, [])

    monkeypatch.setattr(nodes, "investigate_candidates", investigate)
    monkeypatch.setattr(nodes, "decide_candidates", lambda *_: [{"company_id": candidate["company_id"], "group": "needs_review", "final_score": None}])
    graph = build_sourcing_risk_graph(InMemorySaver())

    result = asyncio.run(graph.ainvoke({"run_id": str(uuid4()), "requirement_input": _requirement()}, {"configurable": {"thread_id": "conflict-run"}}))

    assert result["status"] == "NEEDS_REVIEW"
    assert result["next_action"] == "evidence_review_required"


def test_external_provider_failure_keeps_local_candidates_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing local findings after a failed fallback would discard the safe local path."""
    candidate = _candidate()
    policy = _policy() | {"checksum": "policy-id"}
    monkeypatch.setattr(nodes, "parse_requirement", lambda *_: {"status": "ready", "requirement": _requirement()})
    monkeypatch.setattr(nodes, "freeze_policy_snapshot", lambda *_: policy)
    monkeypatch.setattr(nodes, "discover_local_candidates", lambda *_: [candidate])
    monkeypatch.setattr(nodes, "is_candidate_supply_sufficient", lambda *_: False)
    monkeypatch.setattr(nodes, "search_external_provider", lambda *_: (_ for _ in ()).throw(ConnectionError("offline")))
    monkeypatch.setattr(nodes, "_sleep", lambda *_: None)
    monkeypatch.setattr(nodes, "resolve_candidate_identity", lambda _: {"identity_status": "exact", "company_id": candidate["company_id"], "score_eligible": True})

    async def investigate(*_args):
        return ({candidate["company_id"]: [{"dimension": "sanctions", "claim_code": "clear", "freshness_status": "fresh", "conflict_status": "none", "evidence_id": "s-1"}]}, [])

    monkeypatch.setattr(nodes, "investigate_candidates", investigate)
    monkeypatch.setattr(nodes, "validate_evidence_set", lambda *_: {"status": "clear", "reason_codes": [], "score_eligible": True})
    monkeypatch.setattr(nodes, "decide_candidates", lambda *_: [{"company_id": candidate["company_id"], "group": "recommended", "final_score": 88.0}])
    graph = build_sourcing_risk_graph(InMemorySaver())

    result = asyncio.run(graph.ainvoke({"run_id": str(uuid4()), "requirement_input": _requirement()}, {"configurable": {"thread_id": "partial-run"}}))

    assert result["status"] == "PARTIAL"
    assert result["provider_failures"] == ["external_discovery"]
    assert result["decisions"] == [{"company_id": candidate["company_id"], "group": "recommended", "final_score": 88.0}]


def test_sanctions_failure_marks_candidate_needs_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leaving a sanctions-error candidate eligible would permit an unsafe master import."""
    candidate = _candidate()

    async def sanctions(_candidate: dict) -> dict:
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(nodes, "fetch_sanctions", sanctions)
    monkeypatch.setattr(nodes, "_sleep", lambda *_: None)
    monkeypatch.setattr(nodes, "normalize_evidence", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("no persistence in unit test")))

    update = asyncio.run(nodes.investigate_parallel({"run_id": str(uuid4()), "policy_snapshot": _policy(), "candidates": [candidate]}))

    assert update["status"] == "PARTIAL"
    assert update["candidates"] == [{**candidate, "status": "needs_review", "score_eligible": False}]


def test_v2_graph_never_imports_process_local_interrupt_store() -> None:
    """Using the legacy store would make restart recovery lose reviewer pauses."""
    from pathlib import Path

    assert "interrupt_store" not in Path(nodes.__file__).read_text()
