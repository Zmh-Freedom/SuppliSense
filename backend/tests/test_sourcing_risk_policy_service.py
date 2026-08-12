"""Behavior tests for deterministic sourcing-risk policy snapshots."""

from copy import deepcopy

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
