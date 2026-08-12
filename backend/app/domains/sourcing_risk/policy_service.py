"""Deterministic, auditable policy templates for sourcing-risk runs."""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any


WEIGHT_DIMENSIONS = (
    "match",
    "capacity",
    "performance",
    "quality",
    "risk",
    "commercial",
)
REQUIRED_HARD_GATES = {
    "unmatched_category": "rejected",
    "supplier_not_active": "rejected",
    "mandatory_qualification_missing": "rejected",
    "identity_unverified": "needs_review",
    "sanctions_hit": "rejected",
    "sanctions_available": "needs_review",
    "key_evidence_conflict": "needs_review",
}

DEFAULT_POLICY: dict[str, Any] = {
    "template_id": "sourcing-risk-default",
    "template_version": "1.0.0",
    "effective_at": "2026-08-12T00:00:00+00:00",
    "scoring_version": "1.0.0",
    "category": None,
    "minimum_candidate_count": 3,
    "weights": {
        "match": 0.25,
        "capacity": 0.20,
        "performance": 0.15,
        "quality": 0.15,
        "risk": 0.15,
        "commercial": 0.10,
    },
    "missing_penalties": {
        "match": 0.10,
        "capacity": 0.08,
        "performance": 0.06,
        "quality": 0.06,
        "risk": 0.10,
        "commercial": 0.04,
    },
    "stale_penalties": {
        "match": 0.04,
        "capacity": 0.05,
        "performance": 0.04,
        "quality": 0.04,
        "risk": 0.08,
        "commercial": 0.03,
    },
    "freshness_days": {
        "match": 180,
        "capacity": 90,
        "performance": 180,
        "quality": 180,
        "risk": 30,
        "commercial": 90,
    },
    "required_evidence": ["sanctions"],
    "hard_gates": REQUIRED_HARD_GATES,
}

CATEGORY_POLICY_TEMPLATES: dict[str, dict[str, Any]] = {
    "摄像头": {
        "template_id": "sourcing-risk-camera",
        "template_version": "1.0.0",
        "effective_at": "2026-08-12T00:00:00+00:00",
        "category": "摄像头",
        "minimum_candidate_count": 3,
        "weights": {
            "match": 0.30,
            "capacity": 0.20,
            "performance": 0.15,
            "quality": 0.20,
            "risk": 0.10,
            "commercial": 0.05,
        },
        "missing_penalties": {"quality": 0.08},
        "stale_penalties": {"quality": 0.06},
        "freshness_days": {"quality": 90},
    }
}


def resolve_policy_template(category: str) -> dict:
    """Return an independent, validated default-plus-category policy template."""
    policy = _merge_policy(DEFAULT_POLICY, CATEGORY_POLICY_TEMPLATES.get(category, {}))
    validate_policy(policy)
    return policy


def freeze_policy_snapshot(run_id: str, category: str) -> dict:
    """Copy a policy into a complete JSON-safe record that cannot track later templates."""
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty string")

    snapshot = deepcopy(resolve_policy_template(category))
    snapshot["run_id"] = run_id
    snapshot["frozen_at"] = datetime.now(timezone.utc).isoformat()
    snapshot["checksum"] = _snapshot_checksum(snapshot)
    return snapshot


def validate_policy(policy: dict) -> None:
    """Reject policy contracts that could produce unsafe or ambiguous decisions."""
    if not isinstance(policy, dict):
        raise ValueError("policy must be a dictionary")

    for field in ("template_id", "template_version", "effective_at", "scoring_version"):
        if not isinstance(policy.get(field), str) or not policy[field]:
            raise ValueError(f"policy {field} is required")

    if not isinstance(policy.get("minimum_candidate_count"), int) or policy["minimum_candidate_count"] < 1:
        raise ValueError("minimum_candidate_count must be a positive integer")

    _validate_dimensions(policy, "weights", require_total=True)
    _validate_dimensions(policy, "missing_penalties")
    _validate_dimensions(policy, "stale_penalties")
    _validate_dimensions(policy, "freshness_days", positive_integers=True)

    if policy.get("required_evidence") != ["sanctions"]:
        raise ValueError("sanctions evidence must be required")
    if policy.get("hard_gates") != REQUIRED_HARD_GATES:
        raise ValueError("hard gates must match the required deterministic controls")


def _merge_policy(base: dict[str, Any], override: dict[str, Any]) -> dict:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(deepcopy(value))
        else:
            merged[key] = deepcopy(value)
    return merged


def _validate_dimensions(
    policy: dict[str, Any],
    field: str,
    *,
    require_total: bool = False,
    positive_integers: bool = False,
) -> None:
    values = policy.get(field)
    if not isinstance(values, dict) or set(values) != set(WEIGHT_DIMENSIONS):
        raise ValueError(f"{field} must define every scoring dimension")
    if positive_integers:
        if not all(isinstance(value, int) and value > 0 for value in values.values()):
            raise ValueError(f"{field} values must be positive integers")
    elif not all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in values.values()):
        raise ValueError(f"{field} values must be between zero and one")
    if require_total and abs(sum(values.values()) - 1.0) > 1e-9:
        raise ValueError("weights must sum to one")


def _snapshot_checksum(snapshot: dict[str, Any]) -> str:
    payload = {key: value for key, value in snapshot.items() if key != "checksum"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()
