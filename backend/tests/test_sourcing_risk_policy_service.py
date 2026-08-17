"""Behavior tests for deterministic sourcing-risk policy snapshots."""

from copy import deepcopy
import math

import pytest

from app.domains.sourcing_risk import policy_service


def test_default_policy_has_one_total_weight_and_required_sanctions_gate():
    """Changing weights or omitting unavailable-sanctions review breaks safe scoring."""
    policy = policy_service.resolve_policy_template("未知品类")

    assert policy["weights"] == {
        "match": 0.25,
        "capacity": 0.20,
        "performance": 0.15,
        "quality": 0.15,
        "risk": 0.15,
        "commercial": 0.10,
    }
    assert sum(policy["weights"].values()) == pytest.approx(1.0)
    assert policy["minimum_candidate_count"] == 3
    assert policy["required_evidence"] == ["sanctions"]
    assert policy["hard_gates"]["sanctions_available"] == "needs_review"


def test_category_template_overrides_default_without_discarding_default_controls():
    """Replacing instead of merging would drop a default safety gate for camera runs."""
    policy = policy_service.resolve_policy_template("摄像头")

    assert policy["template_id"] == "sourcing-risk-camera"
    assert policy["category"] == "摄像头"
    assert policy["hard_gates"]["sanctions_hit"] == "rejected"
    assert policy["missing_penalties"]["risk"] > 0
    assert policy["stale_penalties"]["risk"] > 0


def test_resolved_policy_is_independent_of_the_template():
    """Returning a template reference would let one caller alter future policy decisions."""
    policy = policy_service.resolve_policy_template("未知品类")
    policy["weights"]["match"] = 0

    assert policy_service.resolve_policy_template("未知品类")["weights"]["match"] == 0.25


def test_snapshot_does_not_change_when_template_changes(monkeypatch):
    """Resolving a later template must not rewrite the policy locked for an old run."""
    snapshot = policy_service.freeze_policy_snapshot("run-id", "摄像头")
    expected_checksum = snapshot["checksum"]
    monkeypatch.setattr(
        policy_service,
        "resolve_policy_template",
        lambda _: {"template_version": "changed"},
    )

    assert snapshot["template_version"] != "changed"
    assert snapshot["checksum"] == expected_checksum
    assert snapshot["run_id"] == "run-id"
    assert snapshot["weights"]["match"] == 0.30


def test_snapshot_is_a_deep_copy_of_the_resolved_policy(monkeypatch):
    """A shallow snapshot would change when the source policy's nested weights change."""
    template = policy_service.resolve_policy_template("摄像头")
    monkeypatch.setattr(policy_service, "resolve_policy_template", lambda _: template)

    snapshot = policy_service.freeze_policy_snapshot("run-id", "摄像头")
    template["weights"]["match"] = 0

    assert snapshot["weights"]["match"] == 0.30


def test_snapshot_is_deeply_immutable_after_it_is_frozen():
    """Allowing nested writes would let an audit record drift after policy lock."""
    snapshot = policy_service.freeze_policy_snapshot("run-id", "摄像头")

    with pytest.raises(TypeError):
        snapshot["weights"]["match"] = 0.25
    with pytest.raises(TypeError):
        snapshot["hard_gates"]["sanctions_hit"] = "needs_review"
    with pytest.raises(AttributeError):
        snapshot["required_evidence"].append("identity")


def test_snapshot_checksum_verification_detects_payload_tampering():
    """Trusting a stored checksum without recomputing it would hide altered decisions."""
    snapshot = policy_service.freeze_policy_snapshot("run-id", "摄像头")
    tampered = dict(snapshot)
    tampered["minimum_candidate_count"] = 99

    assert policy_service.verify_policy_snapshot_checksum(snapshot) is True
    assert policy_service.verify_policy_snapshot_checksum(tampered) is False
    with pytest.raises(ValueError, match="checksum"):
        policy_service.validate_snapshot_checksum(tampered)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda policy: policy["weights"].update(match=0.24),
        lambda policy: policy["hard_gates"].pop("sanctions_available"),
        lambda policy: policy.update(required_evidence=[]),
    ],
)
def test_validate_policy_rejects_unsafe_or_non_deterministic_contracts(mutation):
    """Accepting malformed controls would make scoring or sanctions handling unsafe."""
    policy = deepcopy(policy_service.DEFAULT_POLICY)
    mutation(policy)

    with pytest.raises(ValueError):
        policy_service.validate_policy(policy)


@pytest.mark.parametrize("invalid_weight", [True, math.nan, math.inf, -math.inf])
def test_validate_policy_rejects_boolean_and_non_finite_weights(invalid_weight):
    """Accepting non-real weights makes deterministic score normalization impossible."""
    policy = deepcopy(policy_service.DEFAULT_POLICY)
    policy["weights"]["match"] = invalid_weight

    with pytest.raises(ValueError):
        policy_service.validate_policy(policy)


def test_validate_policy_requires_exact_decimal_weight_normalization():
    """A tolerance-based total would allow policy scores to drift from a normalized total."""
    policy = deepcopy(policy_service.DEFAULT_POLICY)
    policy["weights"]["match"] = 0.2500000001

    with pytest.raises(ValueError, match="sum to one"):
        policy_service.validate_policy(policy)
