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
    claim_fields: list[str] | None = None,
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
    if assessment_status in {"missing_data", "insufficient_data"}:
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
        result["evidence_records"] = _normalize_legacy_records(
            legacy_records,
            tool_name=tool_name,
            entity_id=entity_id,
            dimension=dimension,
            source_type=source_type,
            fallback_collected_at=now,
        )
        _append_claims(result, claim_fields, result["evidence_records"])
        return result
    from app.graphs.agent_core.evidence_ledger import build_evidence_record

    result["evidence_records"] = [build_evidence_record(
        evidence_id=f"{tool_name}:{entity_id}:{dimension}",
        entity_id=entity_id,
        dimension=dimension,
        provider=tool_name,
        source_type=source_type,
        status=_status_value(result.get("status")),
        collected_at=datetime.fromisoformat(now),
        data_mode=data_mode,
        payload={
            key: value
            for key, value in result.items()
            if key not in {"evidence_records", "claims", "evidence", "evidence_refs"}
        },
    ).model_dump(mode="json")]
    _append_claims(result, claim_fields, result["evidence_records"])
    return result


def _normalize_legacy_records(
    records: list[Any],
    *,
    tool_name: str,
    entity_id: str,
    dimension: str,
    source_type: str,
    fallback_collected_at: str,
) -> list[dict[str, Any]]:
    """Convert historical provider records into the complete evidence contract."""
    from app.graphs.agent_core.evidence_ledger import build_evidence_record

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            continue
        status = _status_value(item.get("status"))
        data_mode = "synthetic" if item.get("data_mode") == "synthetic" else "formal"
        collected_at = _parse_datetime(
            item.get("collected_at") or item.get("observed_at") or fallback_collected_at
        )
        facts = item.get("facts")
        payload = dict(facts) if isinstance(facts, dict) else {
            key: value for key, value in item.items()
            if key not in {
                "evidence_id", "entity_id", "dimension", "provider", "source",
                "source_type", "status", "collected_at", "observed_at", "data_mode",
                "endpoint", "query", "raw_payload_ref",
            }
        }
        normalized.append(build_evidence_record(
            evidence_id=str(item.get("evidence_id") or f"{tool_name}:{entity_id}:{dimension}:{index}"),
            entity_id=str(item.get("entity_id") or entity_id),
            dimension=str(item.get("dimension") or dimension),
            provider=str(item.get("provider") or item.get("source") or tool_name),
            source_type=str(item.get("source_type") or item.get("source") or source_type),
            status=status,
            collected_at=collected_at,
            data_mode=data_mode,
            endpoint=str(item["endpoint"]) if item.get("endpoint") else None,
            query=item.get("query") if isinstance(item.get("query"), dict) else None,
            raw_payload_ref=str(item["raw_payload_ref"]) if item.get("raw_payload_ref") else None,
            payload=payload,
        ).model_dump(mode="json"))
    return normalized


def _status_value(value: Any) -> Any:
    from app.graphs.agent_core.evidence_ledger import EvidenceStatus

    try:
        return EvidenceStatus(str(value or EvidenceStatus.AVAILABLE.value))
    except ValueError:
        return EvidenceStatus.UNAVAILABLE


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _append_claims(
    result: dict[str, Any], claim_fields: list[str] | None, records: list[dict[str, Any]]
) -> None:
    """Create field-bound claims only for facts present in the same evidence."""
    if not claim_fields or not records:
        return
    record = records[0]
    facts = record.get("facts") or {}
    claims = result.setdefault("claims", [])
    if not isinstance(claims, list):
        claims = []
        result["claims"] = claims
    subject = str(result.get("company_name") or result.get("supplier_reference") or "企业")
    for path in claim_fields:
        value = _read_path(facts, path)
        if value is _MISSING or value is None:
            continue
        claims.append({
            "claim_id": f"{record['evidence_id']}:claim:{path}",
            "entity_id": record["entity_id"],
            "dimension": record["dimension"],
            "statement": f"{subject} {path} 为 {value}",
            "value": value,
            "fact_path": path,
            "operator": "eq",
            "evidence_refs": [record["evidence_id"]],
            "confidence": 0.85,
        })


_MISSING = object()


def _read_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


__all__ = ["attach_tool_evidence"]
