"""Trusted sanctions-source contracts and read-only adapter boundary.

No production list provider is enabled yet. A source adapter must be added as a
reviewed, versioned contract; arbitrary database flags are not source approval.
"""

from typing import Any


def get_sanctions_source_contracts() -> list[dict[str, Any]]:
    """Return reviewed production source contracts (empty until configured)."""
    return []


def load_sanctions_source(contract: dict, identity: dict) -> dict[str, Any]:
    """Return an explicit not-queried receipt until a provider is implemented."""
    return {
        "evidence": {
            "source": contract.get("source"),
            "source_version": contract.get("source_version"),
            "entity_id": identity.get("company_id"),
            "scope": contract.get("scope"),
            "source_mode": "formal",
            "status": "not_queried",
            "observed_at": None,
            "collected_at": None,
            "expires_at": None,
            "query_receipt": {},
            "coverage_partial": False,
            "conflict": False,
        },
        "matches": [],
    }
