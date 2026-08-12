"""Service-only nodes for the Sourcing Risk V2 orchestration graph."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langgraph.types import interrupt

from app.domains.agent_run import service as agent_run_service
from app.domains.sourcing_risk.decision_service import decide_candidates
from app.domains.sourcing_risk.discovery_service import (
    discover_local_candidates,
    is_candidate_supply_sufficient,
    search_external_provider,
    stage_external_candidates,
)
from app.domains.sourcing_risk.evidence_service import normalize_evidence, validate_evidence_set
from app.domains.sourcing_risk.identity_service import resolve_candidate_identity
from app.domains.sourcing_risk.policy_service import freeze_policy_snapshot
from app.domains.sourcing_risk.requirement_service import parse_requirement

from app.graphs.sourcing_risk_v2.state import SourcingRiskGraphState

PROVIDER_TIMEOUT_SECONDS = 20
PROVIDER_ATTEMPTS = 2
PROVIDER_DIMENSIONS = ("financial", "judicial", "sentiment", "sanctions", "esg", "continuity")
PROVIDER_MAX_CONCURRENCY = 6
_provider_semaphore: asyncio.Semaphore | None = None
_provider_semaphore_loop: asyncio.AbstractEventLoop | None = None


def append_typed_event(run_id: str, event_type: str, payload: dict[str, Any]) -> int | None:
    """Delegate durable event emission to the framework-independent run service."""
    return agent_run_service.append_orchestration_event(run_id, event_type, payload)


def record_orchestration_state(run_id: str, status: str, event_type: str, payload: dict[str, Any]) -> int | None:
    """Keep LangGraph state and the durable run/SSE state machine aligned."""
    return agent_run_service.record_orchestration_state(run_id, status, event_type, payload)


def persist_orchestration_snapshot(
    run_id: str,
    status: str,
    event_type: str,
    payload: dict[str, Any],
    **collections: Any,
) -> dict[str, Any]:
    """Delegate one graph checkpoint's recoverable workbench state to the run service."""
    return agent_run_service.persist_orchestration_snapshot(
        run_id, status, event_type, payload, **collections
    )


async def load_run(state: SourcingRiskGraphState) -> dict[str, Any]:
    """Initialize graph fields without mutating a run record directly."""
    run_id = state["run_id"]
    await _event(run_id, "stage", {"stage": "load_run"}, "CREATED")
    return {
        "status": "CREATED",
        "requirement_id": state.get("requirement_id") or run_id,
        "candidate_ids": list(state.get("candidate_ids", [])),
        "pending_review_ids": [],
        "event_cursor": state.get("event_cursor", 0),
        "error_code": None,
    }


async def parse_requirement_node(state: SourcingRiskGraphState) -> dict[str, Any]:
    """Parse one requirement through the requirement service."""
    input_data = dict(state.get("requirement_input", {}))
    raw_text = str(input_data.pop("requirement_text", ""))
    result = await asyncio.to_thread(parse_requirement, raw_text, input_data)
    if result.get("status") != "ready":
        await _event(state["run_id"], "clarification", {"missing": result.get("missing", [])}, "CLARIFYING")
        clarification = interrupt({"next_action": "clarification_required", "missing": result.get("missing", [])})
        if isinstance(clarification, Mapping):
            input_data = dict(clarification.get("requirement_input", input_data))
            raw_text = str(input_data.pop("requirement_text", raw_text))
            result = await asyncio.to_thread(parse_requirement, raw_text, input_data)
        if result.get("status") == "ready":
            await _event(state["run_id"], "stage", {"stage": "requirement_ready"}, "CREATED")
            return {"status": "CREATED", "requirement": dict(result["requirement"]), "next_action": None}
        return {"status": "CLARIFYING", "next_action": "clarification_required", "error_code": None}
    await _event(state["run_id"], "stage", {"stage": "requirement_ready"}, "CREATED")
    return {"status": "CREATED", "requirement": dict(result["requirement"]), "next_action": None}


async def lock_policy(state: SourcingRiskGraphState) -> dict[str, Any]:
    """Freeze the policy once for this durable run."""
    policy = await asyncio.to_thread(freeze_policy_snapshot, state["run_id"], state["requirement"]["category"])
    await _event(state["run_id"], "policy_locked", {"checksum": policy["checksum"]}, "POLICY_LOCKED")
    return {
        "status": "POLICY_LOCKED",
        "policy_snapshot": dict(policy),
        "policy_snapshot_id": state.get("policy_snapshot_id") or policy["checksum"],
    }


async def local_discovery(state: SourcingRiskGraphState) -> dict[str, Any]:
    """Keep locally found candidates even when a provider fallback is required."""
    candidates = state.get("candidates")
    if candidates is None:
        candidates = await asyncio.to_thread(discover_local_candidates, state["requirement"], state["policy_snapshot"])
    candidates = _with_candidate_keys(candidates)
    sufficient = is_candidate_supply_sufficient(candidates, state["requirement"], state["policy_snapshot"])
    await _snapshot_event(
        state["run_id"], "LOCAL_SEARCHING", "discovery",
        {"source": "local", "count": len(candidates), "sufficient": sufficient},
        candidates=candidates,
    )
    return {
        "status": "LOCAL_SEARCHING",
        "candidates": candidates,
        "candidate_ids": _candidate_ids(candidates),
        "next_action": "external_discovery_required" if not sufficient else None,
    }


async def external_discovery(state: SourcingRiskGraphState) -> dict[str, Any]:
    """Run only the external discovery adapter under the provider safety policy."""
    local_candidates = list(state.get("candidates", []))
    try:
        found = await _call_provider("external_discovery", search_external_provider, state["requirement"])
    except Exception as exc:
        await _event(
            state["run_id"],
            "provider_failed",
            {"provider": "external_discovery", "error": type(exc).__name__},
            "LOCAL_SEARCHING",
        )
        return {"status": "LOCAL_SEARCHING", "external_candidates": [], "provider_failures": ["external_discovery"]}
    staged = stage_external_candidates(state["run_id"], found)
    candidates = _with_candidate_keys([*local_candidates, *staged])
    await _snapshot_event(
        state["run_id"], "EXTERNAL_REVIEW", "discovery",
        {"source": "external_staged", "count": len(staged)}, candidates=candidates,
    )
    return {
        "status": "EXTERNAL_REVIEW",
        "candidates": candidates,
        "external_candidates": staged,
        "candidate_ids": _candidate_ids(candidates),
    }


async def identity_resolution(state: SourcingRiskGraphState) -> dict[str, Any]:
    """Resolve exact identities before deciding whether human review is needed."""
    resolved: list[dict[str, Any]] = []
    pending: list[str] = []
    for candidate in state.get("candidates", []):
        identity = await asyncio.to_thread(resolve_candidate_identity, candidate)
        item = {**candidate, **identity}
        company_id = item.get("company_id")
        if item.get("identity_status") != "exact" or not company_id:
            pending.append(_review_id(candidate, identity))
        resolved.append(item)
    if pending:
        await _snapshot_event(state["run_id"], "IDENTITY_RESOLVING", "identity_resolving", {"count": len(resolved)}, candidates=resolved)
        await _snapshot_event(state["run_id"], "IDENTITY_REVIEW", "identity_review", {"pending_review_ids": pending}, candidates=resolved)
        return {"status": "IDENTITY_REVIEW", "next_action": "identity_review_required", "pending_review_ids": pending, "candidates": resolved}
    await _snapshot_event(state["run_id"], "IDENTITY_RESOLVING", "identity_resolved", {"count": len(resolved)}, candidates=resolved)
    return {"status": "IDENTITY_RESOLVING", "next_action": None, "pending_review_ids": [], "candidates": resolved, "candidate_ids": _candidate_ids(resolved)}


async def identity_review(state: SourcingRiskGraphState) -> dict[str, Any]:
    """Durably pause for human identity confirmation using LangGraph interrupts."""
    review = interrupt({"next_action": "identity_review_required", "pending_review_ids": state.get("pending_review_ids", [])})
    resolutions = review.get("identity_resolutions", {}) if isinstance(review, Mapping) else {}
    resolved, pending = _apply_identity_resolutions(state.get("candidates", []), resolutions)
    if pending:
        return {"status": "IDENTITY_REVIEW", "next_action": "identity_review_required", "pending_review_ids": pending, "candidates": resolved}
    await _snapshot_event(
        state["run_id"], "IDENTITY_RESOLVING", "identity_resolved", {"count": len(resolved)},
        candidates=resolved,
    )
    return {"status": "IDENTITY_RESOLVING", "next_action": None, "pending_review_ids": [], "candidates": resolved, "candidate_ids": _candidate_ids(resolved)}


async def investigate_parallel(state: SourcingRiskGraphState) -> dict[str, Any]:
    """Gather independent provider evidence with isolated failures."""
    evidence, failures = await investigate_candidates(state.get("candidates", []))
    normalized_evidence: dict[str, list[dict[str, Any]]] = {}
    for candidate in state.get("candidates", []):
        company_id = candidate.get("company_id")
        if not company_id:
            continue
        normalized_evidence[str(company_id)] = await _normalize_candidate_evidence(
            state["run_id"], str(company_id), evidence.get(str(company_id), []), state["policy_snapshot"]
        )
    failures = sorted(set([*state.get("provider_failures", []), *failures]))
    candidates = _mark_sanctions_failures_for_review(state.get("candidates", []), normalized_evidence)
    await _snapshot_event(
        state["run_id"], "INVESTIGATING", "investigation", {"failed_dimensions": failures},
        candidates=candidates, evidence_by_company_id=normalized_evidence,
    )
    return {
        "status": "INVESTIGATING",
        "candidates": candidates,
        "evidence_by_company_id": normalized_evidence,
        "provider_failures": failures,
    }


async def _normalize_candidate_evidence(
    run_id: str, company_id: str, evidence: list[dict[str, Any]], policy: dict[str, Any]
) -> list[dict[str, Any]]:
    """Persist provider evidence only through the evidence service contract."""
    normalized: list[dict[str, Any]] = []
    for item in evidence:
        try:
            record = await asyncio.to_thread(
                normalize_evidence, run_id, company_id, item["dimension"], item, policy=policy
            )
        except Exception:
            normalized.append(item)
        else:
            normalized.append(record.model_dump(mode="json"))
    return normalized


async def validate_evidence(state: SourcingRiskGraphState) -> dict[str, Any]:
    """Use evidence service gates before scoring any candidate."""
    reviews = {
        company_id: validate_evidence_set(evidence, state["policy_snapshot"])
        for company_id, evidence in state.get("evidence_by_company_id", {}).items()
    }
    requires_review = any(result["status"] == "needs_review" for result in reviews.values())
    if requires_review:
        await _snapshot_event(
            state["run_id"], "EVIDENCE_REVIEW", "evidence_validated", {"requires_review": True},
            evidence_reviews=reviews,
        )
    else:
        await _snapshot_event(
            state["run_id"], state.get("status", "INVESTIGATING"), "evidence_validated",
            {"requires_review": False}, evidence_reviews=reviews,
        )
    return {
        "status": "EVIDENCE_REVIEW" if requires_review else state.get("status", "INVESTIGATING"),
        "evidence_reviews": reviews,
        "next_action": "evidence_review_required" if requires_review else None,
    }


async def score_candidates(state: SourcingRiskGraphState) -> dict[str, Any]:
    """Delegate deterministic scoring to the decision service."""
    decisions = decide_candidates(state["requirement"], state["policy_snapshot"], state.get("candidates", []), state.get("evidence_by_company_id", {}))
    await _snapshot_event(
        state["run_id"], "SCORING", "decision", {"count": len(decisions)},
        candidates=state.get("candidates", []), decisions=decisions,
    )
    return {"status": "SCORING", "decisions": decisions}


async def ready_for_review(state: SourcingRiskGraphState) -> dict[str, Any]:
    """Finish in a review-safe state; this graph never writes supplier masters."""
    decisions = state.get("decisions", [])
    failures = state.get("provider_failures", [])
    has_review = any(item.get("group") == "needs_review" for item in decisions)
    status = "NEEDS_REVIEW" if has_review else "PARTIAL" if failures else "READY_FOR_REVIEW"
    next_action = "evidence_review_required" if has_review else "review_required"
    await _event(state["run_id"], "ready_for_review", {"provider_failures": failures}, status)
    return {"status": status, "next_action": next_action}


def route_after_requirement(state: SourcingRiskGraphState) -> str:
    return "end" if state.get("status") == "CLARIFYING" else "lock_policy"


def route_after_discovery(state: SourcingRiskGraphState) -> str:
    return "external_discovery" if state.get("next_action") == "external_discovery_required" else "identity_resolution"


async def investigate_candidates(candidates: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Investigate canonical candidates concurrently; unresolved candidates stay review-only."""
    results = await asyncio.gather(*(investigate_candidate(candidate) for candidate in candidates))
    evidence: dict[str, list[dict[str, Any]]] = {}
    failures: list[str] = []
    for candidate, (candidate_evidence, candidate_failures) in zip(candidates, results, strict=True):
        company_id = candidate.get("company_id")
        if company_id:
            evidence[str(company_id)] = candidate_evidence
        failures.extend(candidate_failures)
    return evidence, sorted(set(failures))


async def investigate_candidate(candidate: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch all provider dimensions concurrently and convert failures into auditable evidence."""
    tasks = [asyncio.create_task(_fetch_dimension(dimension, candidate)) for dimension in PROVIDER_DIMENSIONS]
    outcomes = await asyncio.gather(*tasks)
    evidence: list[dict[str, Any]] = []
    failures: list[str] = []
    for dimension, outcome in outcomes:
        if isinstance(outcome, Exception):
            failures.append(dimension)
            evidence.append(_unavailable_evidence(dimension))
        else:
            evidence.append({"dimension": dimension, **outcome})
    return evidence, failures


async def _fetch_dimension(dimension: str, candidate: dict[str, Any]) -> tuple[str, dict[str, Any] | Exception]:
    provider = _provider_for(dimension)
    try:
        return dimension, await _call_provider(dimension, provider, candidate)
    except Exception as exc:  # evidence must retain the individual provider failure
        return dimension, exc


async def _call_provider(name: str, provider: Callable[..., Any], *args: Any) -> Any:
    """Use exactly one retry with exponential backoff and a hard per-attempt limit."""
    last_error: Exception | None = None
    async with _get_provider_semaphore():
        for attempt in range(PROVIDER_ATTEMPTS):
            try:
                if inspect.iscoroutinefunction(provider):
                    result = provider(*args)
                else:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(provider, *args), timeout=PROVIDER_TIMEOUT_SECONDS
                    )
                    if inspect.isawaitable(result):
                        result = await asyncio.wait_for(result, timeout=PROVIDER_TIMEOUT_SECONDS)
                    return result
                return await asyncio.wait_for(result, timeout=PROVIDER_TIMEOUT_SECONDS)
            except (TimeoutError, ConnectionError, OSError) as exc:
                last_error = exc
                if attempt + 1 < PROVIDER_ATTEMPTS:
                    await _maybe_await(_sleep(2**attempt))
                    continue
                raise
    raise last_error or RuntimeError(f"{name} provider failed")


def _get_provider_semaphore() -> asyncio.Semaphore:
    """Share one bounded provider budget across every candidate in this event loop."""
    global _provider_semaphore, _provider_semaphore_loop

    loop = asyncio.get_running_loop()
    if _provider_semaphore is None or _provider_semaphore_loop is not loop:
        _provider_semaphore = asyncio.Semaphore(PROVIDER_MAX_CONCURRENCY)
        _provider_semaphore_loop = loop
    return _provider_semaphore


async def _sleep(delay: float) -> None:
    await asyncio.sleep(delay)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def fetch_financial(candidate: dict[str, Any]) -> dict[str, Any]:
    return {"claim_code": "unknown", "source_type": "financial_provider", "summary": ""}


async def fetch_judicial(candidate: dict[str, Any]) -> dict[str, Any]:
    return {"claim_code": "unknown", "source_type": "judicial_provider", "summary": ""}


async def fetch_sentiment(candidate: dict[str, Any]) -> dict[str, Any]:
    return {"claim_code": "unknown", "source_type": "sentiment_provider", "summary": ""}


async def fetch_sanctions(candidate: dict[str, Any]) -> dict[str, Any]:
    return {"claim_code": "unknown", "source_type": "sanctions_provider", "summary": ""}


async def fetch_esg(candidate: dict[str, Any]) -> dict[str, Any]:
    return {"claim_code": "unknown", "source_type": "esg_provider", "summary": ""}


async def fetch_continuity(candidate: dict[str, Any]) -> dict[str, Any]:
    return {"claim_code": "unknown", "source_type": "continuity_provider", "summary": ""}


def _provider_for(dimension: str) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    return {
        "financial": fetch_financial,
        "judicial": fetch_judicial,
        "sentiment": fetch_sentiment,
        "sanctions": fetch_sanctions,
        "esg": fetch_esg,
        "continuity": fetch_continuity,
    }[dimension]


def _unavailable_evidence(dimension: str) -> dict[str, Any]:
    return {"dimension": dimension, "claim_code": "unavailable", "freshness_status": "unknown", "conflict_status": "unknown", "source_type": "provider_error"}


def _apply_identity_resolutions(candidates: list[dict[str, Any]], resolutions: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    resolved: list[dict[str, Any]] = []
    pending: list[str] = []
    for candidate in candidates:
        review_id = _review_id(candidate, candidate)
        company_id = resolutions.get(review_id)
        if isinstance(company_id, str) and company_id:
            resolved.append({**candidate, "company_id": company_id, "identity_company_id": company_id, "identity_status": "exact", "score_eligible": True, "identity_review": False})
        else:
            pending.append(review_id)
            resolved.append(candidate)
    return resolved, pending


def _review_id(candidate: Mapping[str, Any], identity: Mapping[str, Any]) -> str:
    return str(candidate.get("company_id") or candidate.get("supplier_id") or candidate.get("supplier_name") or identity.get("identity_status"))


def _candidate_ids(candidates: list[dict[str, Any]]) -> list[str]:
    return [str(candidate["company_id"]) for candidate in candidates if candidate.get("company_id")]


def _mark_sanctions_failures_for_review(
    candidates: list[dict[str, Any]], evidence_by_company_id: Mapping[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Keep a sanctions-provider failure review-only before any final decision."""
    updated: list[dict[str, Any]] = []
    for candidate in candidates:
        evidence = evidence_by_company_id.get(str(candidate.get("company_id")), [])
        unavailable = any(
            item.get("dimension") == "sanctions"
            and item.get("claim_code") in {"unavailable", "unknown", "error"}
            for item in evidence
        )
        updated.append({**candidate, **({"status": "needs_review", "score_eligible": False} if unavailable else {})})
    return updated


async def _event(run_id: str, event_type: str, payload: dict[str, Any], status: str | None = None) -> None:
    if status is not None:
        await asyncio.to_thread(record_orchestration_state, run_id, status, event_type, payload)
        return
    await asyncio.to_thread(append_typed_event, run_id, event_type, payload)


async def _snapshot_event(
    run_id: str, status: str, event_type: str, payload: dict[str, Any], **collections: Any,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        persist_orchestration_snapshot, run_id, status, event_type, payload, **collections
    )


def _with_candidate_keys(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **candidate,
            "candidate_key": str(
                candidate.get("candidate_key")
                or candidate.get("supplier_id")
                or candidate.get("company_id")
                or candidate.get("supplier_name")
            ),
        }
        for candidate in candidates
    ]
