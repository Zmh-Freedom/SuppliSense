"""Immutable assessment identity, policy and evidence contracts."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.domains.risk.assessment_contracts import canonical_hash, validate_manifest
from app.domains.risk.assessment_policy import current_policy


def test_policy_is_a_detached_immutable_snapshot():
    first = current_policy(["financial"])
    original = deepcopy(first)
    first["weights"]["financial"] = 999
    assert current_policy(["financial"]) == original
    assert original["required_dimensions"] == ["financial"]
    assert original["score_direction"] == "higher_is_safer"


def test_policy_hash_changes_when_missing_policy_or_thresholds_change():
    original = current_policy(["financial"])
    changed = {**original, "missing_policy": "zero_risk"}
    assert canonical_hash(changed) != canonical_hash(original)


def test_empty_evidence_requires_a_successful_query_receipt():
    entry = {"evidence_id": "empty", "dimension": "judicial", "status": "empty", "source": "provider", "content_hash": "a" * 64, "raw_payload_ref": "payload:empty", "collected_at": "2026-10-19T00:00:00+00:00", "observed_at": None}
    with pytest.raises(ValueError, match="回执"):
        validate_manifest([entry])
    assert validate_manifest([{**entry, "query_receipt": {"status": "success", "record_count": 0}}])
