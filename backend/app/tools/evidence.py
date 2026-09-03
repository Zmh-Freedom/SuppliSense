"""Normalize domain-tool observations into the common evidence envelope."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def attach_tool_evidence(
    payload: dict[str, Any],
    *,
    tool_name: str,
    entity_id: str,
    dimension: str,
    source_type: str = "domain_service_result",
) -> dict[str, Any]:
    """Attach auditable evidence without changing the domain service contract.

    Existing provider evidence is preserved.  The fallback record describes the
    returned domain-service observation itself, so it is not confused with an
    independent external source; Task 13 will bind individual claims to its
    facts.  Error-only payloads are explicit non-success outcomes and receive no
    evidence record.
    """
    result = dict(payload)
    assessment_status = result.get("assessment_status")
    if assessment_status == "missing_supplier":
        result.setdefault("status", "not_found")
        return result
    if assessment_status == "missing_data":
        result.setdefault("status", "unavailable")
        return result
    if result.get("status") in {"not_found", "unavailable", "failed", "invalid", "denied"}:
        return result
    if result.get("error") and not any(
        result.get(key) for key in ("risk_score", "total_score", "companies", "results", "candidates", "items")
    ):
        result.setdefault("status", "not_found")
        return result
    if result.get("evidence_records") or result.get("evidence_refs"):
        return result

    data_mode = "synthetic" if result.get("data_mode") == "synthetic" else "formal"
    if result.get("assessment_data_mode") == "demo":
        data_mode = "synthetic"
    now = datetime.now(timezone.utc).isoformat()
    legacy_records = result.get("evidence")
    if isinstance(legacy_records, list) and legacy_records:
        result["evidence_records"] = [
            {
                "evidence_id": str(item.get("evidence_id") or f"{tool_name}:{entity_id}:{dimension}:{index}"),
                "entity_id": str(item.get("entity_id") or entity_id),
                "dimension": str(item.get("dimension") or dimension),
                "provider": str(item.get("provider") or item.get("source") or tool_name),
                "source_type": str(item.get("source_type") or item.get("source") or source_type),
                "status": str(item.get("status") or "available"),
                "collected_at": str(item.get("collected_at") or item.get("observed_at") or now),
                "data_mode": str(item.get("data_mode") or data_mode),
                "facts": dict(item),
            }
            for index, item in enumerate(legacy_records)
            if isinstance(item, dict)
        ]
        return result
    result["evidence_records"] = [{
        "evidence_id": f"{tool_name}:{entity_id}:{dimension}",
        "entity_id": entity_id,
        "dimension": dimension,
        "provider": tool_name,
        "source_type": source_type,
        "status": "available",
        "collected_at": now,
        "data_mode": data_mode,
        "facts": {key: value for key, value in result.items() if key not in {"evidence_records", "claims"}},
    }]
    return result


__all__ = ["attach_tool_evidence"]
