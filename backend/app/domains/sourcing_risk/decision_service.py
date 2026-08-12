"""Pure, deterministic candidate decisions for sourcing-risk runs."""

from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domains.sourcing_risk.evidence_service import validate_evidence_set


GROUP_ORDER = {"recommended": 0, "alternative": 1, "needs_review": 2, "rejected": 3}
RECOMMENDED_SCORE = Decimal("80")
ALTERNATIVE_SCORE = Decimal("60")
FINANCIAL_DIMENSION = "risk"


def decide_candidates(
    requirement: dict,
    policy_snapshot: Mapping[str, Any],
    candidates: list[dict],
    evidence_by_company_id: Mapping[str, list[dict]],
) -> list[dict]:
    """Return auditable candidate groups using gates before deterministic scoring."""
    weights = _validated_weights(policy_snapshot)
    decisions = [
        _decide_candidate(requirement, policy_snapshot, weights, candidate, evidence_by_company_id)
        for candidate in candidates
    ]
    return sorted(
        decisions,
        key=lambda item: (
            GROUP_ORDER[item["group"]],
            -(item["final_score"] if item["final_score"] is not None else 0),
            item["company_id"],
        ),
    )


def _decide_candidate(
    requirement: dict,
    policy_snapshot: Mapping[str, Any],
    weights: dict[str, Decimal],
    candidate: dict,
    evidence_by_company_id: Mapping[str, list[dict]],
) -> dict:
    company_id = str(candidate.get("company_id") or "")
    evidence = list(evidence_by_company_id.get(company_id, []))
    evidence_outcome = validate_evidence_set(evidence, dict(policy_snapshot))
    evidence_ids = sorted(
        str(item["evidence_id"])
        for item in evidence
        if item.get("evidence_id") is not None
    )
    gate = _first_hard_gate(requirement, policy_snapshot, candidate, evidence, evidence_outcome)
    base = {
        "company_id": company_id,
        "dimension_scores": {},
        "missing_data_penalty": 0.0,
        "stale_data_penalty": 0.0,
        "confidence": _confidence(evidence, 0, len(weights)),
        "reason_codes": gate[1] if gate else [],
        "evidence_ids": evidence_ids,
        "policy_snapshot": deepcopy(dict(policy_snapshot)),
    }
    if gate:
        return {**base, "group": gate[0], "final_score": None}

    scores, missing_dimensions = _dimension_scores(candidate, weights)
    stale_dimensions = _stale_dimensions(candidate, evidence)
    missing_penalty = _penalty(policy_snapshot, "missing_penalties", missing_dimensions)
    stale_penalty = _penalty(policy_snapshot, "stale_penalties", stale_dimensions - set(missing_dimensions))
    final_score = _clamp(
        sum(weights[name] * scores[name] for name in weights) - missing_penalty - stale_penalty
    )
    reason_codes = [
        *(_missing_reason_code(name) for name in missing_dimensions),
        *(f"STALE_{name.upper()}_DATA" for name in sorted(stale_dimensions - set(missing_dimensions))),
    ]
    score = float(final_score)
    return {
        **base,
        "group": _score_group(final_score),
        "final_score": score,
        "dimension_scores": {name: float(scores[name]) for name in weights},
        "missing_data_penalty": float(missing_penalty),
        "stale_data_penalty": float(stale_penalty),
        "confidence": _confidence(evidence, len(weights) - len(missing_dimensions), len(weights)),
        "reason_codes": reason_codes,
    }


def _first_hard_gate(
    requirement: dict,
    policy_snapshot: Mapping[str, Any],
    candidate: dict,
    evidence: list[dict],
    evidence_outcome: dict,
) -> tuple[str, list[str]] | None:
    hard_gates = policy_snapshot.get("hard_gates")
    if not isinstance(hard_gates, Mapping):
        raise ValueError("policy snapshot must define hard gates")
    triggered = {
        "unmatched_category": (not _matches_category(candidate, requirement.get("category")), ["UNMATCHED_CATEGORY"]),
        "supplier_not_active": (candidate.get("active") is not True, ["SUPPLIER_NOT_ACTIVE"]),
        "mandatory_qualification_missing": (
            not _has_qualifications(candidate, requirement.get("qualifications")),
            ["MANDATORY_QUALIFICATION_MISSING"],
        ),
        "identity_unverified": (
            candidate.get("identity_status") != "exact" or candidate.get("score_eligible") is not True,
            ["IDENTITY_UNVERIFIED"],
        ),
        "sanctions_hit": (
            any(
                item.get("dimension") == "sanctions"
                and item.get("claim_code") in {"hit", "match"}
                for item in evidence
            ),
            ["SANCTIONS_HIT"],
        ),
        "sanctions_available": (
            evidence_outcome["status"] != "clear" or not evidence_outcome["score_eligible"],
            list(evidence_outcome["reason_codes"]),
        ),
        "key_evidence_conflict": (
            "KEY_EVIDENCE_CONFLICT" in evidence_outcome["reason_codes"],
            list(evidence_outcome["reason_codes"]),
        ),
    }
    for gate_name, outcome in hard_gates.items():
        if gate_name not in triggered:
            continue
        is_triggered, reason_codes = triggered[gate_name]
        if is_triggered:
            return str(outcome), reason_codes
    return None


def _validated_weights(policy_snapshot: Mapping[str, Any]) -> dict[str, Decimal]:
    raw_weights = policy_snapshot.get("weights")
    if not isinstance(raw_weights, Mapping) or not raw_weights:
        raise ValueError("policy snapshot must define weights")
    weights = {str(name): _decimal(value, "weight") for name, value in raw_weights.items()}
    if any(value < 0 for value in weights.values()) or sum(weights.values()) != Decimal("1"):
        raise ValueError("policy weights must sum to one exactly")
    return weights


def _dimension_scores(candidate: dict, weights: Mapping[str, Decimal]) -> tuple[dict[str, Decimal], list[str]]:
    raw_scores = candidate.get("dimension_scores")
    if not isinstance(raw_scores, Mapping):
        raw_scores = {}
    scores: dict[str, Decimal] = {}
    missing: list[str] = []
    for name in weights:
        value = raw_scores.get(name)
        if value is None:
            scores[name] = Decimal("0")
            missing.append(name)
            continue
        score = _decimal(value, f"{name} score")
        if not Decimal("0") <= score <= Decimal("100"):
            raise ValueError(f"{name} score must be between zero and 100")
        scores[name] = score
    return scores, missing


def _stale_dimensions(candidate: dict, evidence: list[dict]) -> set[str]:
    stale = {
        str(name)
        for name in candidate.get("stale_dimensions", [])
        if isinstance(name, str)
    }
    evidence_dimension_map = {"financial": "risk", "continuity": "capacity"}
    stale.update(
        evidence_dimension_map[item["dimension"]]
        for item in evidence
        if item.get("freshness_status") == "stale" and item.get("dimension") in evidence_dimension_map
    )
    return stale


def _penalty(policy: Mapping[str, Any], field: str, dimensions: object) -> Decimal:
    penalties = policy.get(field)
    if not isinstance(penalties, Mapping):
        raise ValueError(f"policy snapshot must define {field}")
    return sum((_decimal(penalties.get(name), field) * 100 for name in dimensions), Decimal("0"))


def _matches_category(candidate: dict, required_category: object) -> bool:
    if not isinstance(required_category, str) or not required_category:
        return True
    categories = candidate.get("categories")
    if isinstance(categories, str):
        categories = [categories]
    return isinstance(categories, (list, tuple)) and any(
        isinstance(category, str) and required_category.casefold() in category.casefold()
        for category in categories
    )


def _has_qualifications(candidate: dict, required_qualifications: object) -> bool:
    if not isinstance(required_qualifications, str) or not required_qualifications:
        return True
    qualifications = candidate.get("qualifications")
    if isinstance(qualifications, str):
        qualifications = [qualifications]
    required = [item.strip().casefold() for item in required_qualifications.replace("，", ",").split(",") if item.strip()]
    return isinstance(qualifications, (list, tuple)) and all(
        any(isinstance(item, str) and value in item.casefold() for item in qualifications)
        for value in required
    )


def _missing_reason_code(dimension: str) -> str:
    if dimension == FINANCIAL_DIMENSION:
        return "MISSING_FINANCIAL_DATA"
    return f"MISSING_{dimension.upper()}_DATA"


def _score_group(score: Decimal) -> str:
    if score >= RECOMMENDED_SCORE:
        return "recommended"
    if score >= ALTERNATIVE_SCORE:
        return "alternative"
    return "rejected"


def _confidence(evidence: list[dict], scored_dimensions: int, total_dimensions: int) -> float:
    coverage = Decimal(scored_dimensions) / Decimal(total_dimensions) if total_dimensions else Decimal("0")
    values = [_decimal(item.get("confidence", 1), "evidence confidence") for item in evidence]
    evidence_confidence = sum(values, Decimal("0")) / len(values) if values else Decimal("0")
    return float(coverage * evidence_confidence)


def _clamp(score: Decimal) -> Decimal:
    return min(Decimal("100"), max(Decimal("0"), score))


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{field} must be a finite real number")
    try:
        decimal = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a finite real number") from exc
    if not decimal.is_finite():
        raise ValueError(f"{field} must be a finite real number")
    return decimal
