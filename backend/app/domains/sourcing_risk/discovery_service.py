"""Local-first, side-effect-free supplier candidate discovery."""

from typing import Any

from app.domains.sourcing.supplier_repo import search_for_sourcing_v2


def search_local_suppliers(requirement: dict, policy: dict) -> list[dict]:
    """Search the local library; policy is accepted for a stable orchestration seam."""
    del policy
    return search_for_sourcing_v2(requirement)


def search_external_provider(requirement: dict) -> list[dict]:
    """External discovery adapter, intentionally deferred until provider policy exists."""
    del requirement
    return []


def discover_local_candidates(requirement: dict, policy: dict) -> list[dict]:
    """Return local candidates only for callers that must never query providers."""
    return search_local_suppliers(requirement, policy)


def is_candidate_supply_sufficient(
    candidates: list[dict], requirement: dict, policy: dict
) -> bool:
    """Require both the configured count and representation of each constraint."""
    if len(candidates) < policy["minimum_candidate_count"]:
        return False

    for field, candidate_field in (
        ("category", "categories"),
        ("specification", "categories"),
        ("region", "regions"),
        ("qualifications", "qualifications"),
    ):
        for value in _requirement_values(requirement.get(field)):
            if not any(_contains_value(candidate.get(candidate_field), value) for candidate in candidates):
                return False
    return True


def stage_external_candidates(run_id: str, candidates: list[dict]) -> list[dict]:
    """Mark provider findings as review-only without resolving or creating identities."""
    return [
        {
            **candidate,
            **({"run_id": run_id} if run_id else {}),
            "status": "staged_candidate",
            "supplier_id": None,
            "company_id": None,
        }
        for candidate in candidates
    ]


def discover_candidates(requirement: dict, policy: dict) -> dict:
    """Prefer sufficient local results and preserve them when external fallback is used."""
    local_candidates = discover_local_candidates(requirement, policy)
    if is_candidate_supply_sufficient(local_candidates, requirement, policy):
        return {
            "source": "local",
            "local_candidates": local_candidates,
            "external_candidates": [],
            "external_status": "not_required",
        }

    try:
        external_candidates = stage_external_candidates(
            str(requirement.get("run_id", "")), search_external_provider(requirement)
        )
    except Exception as exc:
        return {
            "source": "local",
            "local_candidates": local_candidates,
            "external_candidates": [],
            "external_status": f"failed:{exc.__class__.__name__}",
        }
    return {
        "source": "local_and_external",
        "local_candidates": local_candidates,
        "external_candidates": external_candidates,
        "external_status": "staged",
    }


def _requirement_values(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]


def _contains_value(values: Any, requested: str) -> bool:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return False
    normalized = requested.casefold()
    return any(isinstance(value, str) and normalized in value.casefold() for value in values)
