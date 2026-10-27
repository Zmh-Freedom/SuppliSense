"""Fixed, synthetic source facts for evidence-validity unit tests."""
from datetime import datetime, timezone
from typing import Any

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)
ENTITY_ID = "company-fixture-a"
SCOPE = "fixture-scope"


def source_evidence(**overrides: Any) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "source": "fixture-list-a",
        "source_version": "fixture-v1",
        "entity_id": ENTITY_ID,
        "scope": SCOPE,
        "source_mode": "formal",
        "status": "available",
        "observed_at": "2026-10-22T00:00:00Z",
        "collected_at": "2026-10-22T00:05:00Z",
        "expires_at": "2026-10-24T00:00:00Z",
        "query_receipt": {"status": "success", "record_count": 1},
        "coverage_partial": False,
        "conflict": False,
    }
    evidence.update(overrides)
    return evidence
