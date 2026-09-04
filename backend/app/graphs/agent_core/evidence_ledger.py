"""Evidence normalization and Claim-Evidence validation for Agent answers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceStatus(str, Enum):
    AVAILABLE = "available"
    CONFIRMED_EMPTY = "confirmed_empty"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    CONFLICTING = "conflicting"
    SYNTHETIC = "synthetic"


class EvidenceRecord(BaseModel):
    """A normalized, source-traceable observation."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=255)
    entity_id: str = Field(min_length=1, max_length=255)
    dimension: str = Field(min_length=1, max_length=64)
    provider: str = Field(min_length=1, max_length=128)
    source_type: str = Field(min_length=1, max_length=64)
    endpoint: str | None = None
    query: dict[str, Any] = Field(default_factory=dict)
    status: EvidenceStatus
    collected_at: datetime
    valid_until: datetime | None = None
    data_mode: str = Field(default="formal", pattern="^(formal|synthetic)$")
    content_hash: str = Field(min_length=1, max_length=128)
    raw_payload_ref: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)


class Claim(BaseModel):
    """A candidate deterministic statement awaiting evidence validation."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=255)
    entity_id: str = Field(min_length=1, max_length=255)
    dimension: str = Field(min_length=1, max_length=64)
    statement: str = Field(min_length=1, max_length=4000)
    value: str | float | int | bool | None = None
    fact_path: str | None = Field(default=None, min_length=1, max_length=255)
    operator: Literal["eq", "neq", "gt", "gte", "lt", "lte", "contains", "exists"] = "eq"
    unit: str | None = Field(default=None, max_length=64)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ValidatedClaim(Claim):
    """Claim with a deterministic validation result."""

    validation_status: str = Field(pattern="^(supported|partial|conflicting|unsupported)$")
    validation_reasons: list[str] = Field(default_factory=list)


class ClaimValidationResult(BaseModel):
    claim: ValidatedClaim
    valid_evidence_refs: list[str] = Field(default_factory=list)
    invalid_evidence_refs: list[str] = Field(default_factory=list)


class EvidenceCoverage(BaseModel):
    required_dimensions: list[str] = Field(default_factory=list)
    covered_dimensions: list[str] = Field(default_factory=list)
    missing_dimensions: list[str] = Field(default_factory=list)
    required_items: list[str] = Field(default_factory=list)
    covered_items: list[str] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    coverage_ratio: float = Field(ge=0.0, le=1.0)


class EvidenceLedger:
    """In-memory run ledger; durable persistence is supplied by the Run store."""

    def __init__(self, records: list[EvidenceRecord] | None = None) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        for record in records or []:
            self.add(record)

    @property
    def records(self) -> list[EvidenceRecord]:
        return list(self._records.values())

    def add(self, record: EvidenceRecord) -> EvidenceRecord:
        """Add one record idempotently; a conflicting duplicate is explicit."""
        existing = self._records.get(record.evidence_id)
        if existing and existing.content_hash != record.content_hash:
            self._records[record.evidence_id] = record.model_copy(
                update={"status": EvidenceStatus.CONFLICTING}
            )
        else:
            self._records[record.evidence_id] = record
        return self._records[record.evidence_id]

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        return self._records.get(evidence_id)

    def validate_claim(self, claim: Claim, *, now: datetime | None = None) -> ClaimValidationResult:
        """Bind a claim only to matching, current, non-synthetic evidence."""
        current_time = now or datetime.now(timezone.utc)
        records = [self.get(ref) for ref in claim.evidence_refs]
        reasons: list[str] = []
        invalid_refs: list[str] = []
        valid_refs: list[str] = []
        if not claim.evidence_refs:
            reasons.append("claim_missing_evidence")
        for ref, record in zip(claim.evidence_refs, records, strict=True):
            if record is None:
                invalid_refs.append(ref)
                reasons.append(f"evidence_not_found:{ref}")
                continue
            if record.entity_id != claim.entity_id:
                invalid_refs.append(ref)
                reasons.append(f"entity_mismatch:{ref}")
                continue
            if record.dimension != claim.dimension:
                invalid_refs.append(ref)
                reasons.append(f"dimension_mismatch:{ref}")
                continue
            if record.valid_until and current_time > record.valid_until and record.status == EvidenceStatus.AVAILABLE:
                invalid_refs.append(ref)
                reasons.append(f"evidence_expired:{ref}")
                continue
            if record.status == EvidenceStatus.CONFLICTING:
                invalid_refs.append(ref)
                reasons.append(f"evidence_conflicting:{ref}")
                continue
            if record.status in {EvidenceStatus.MISSING, EvidenceStatus.UNAVAILABLE, EvidenceStatus.STALE}:
                invalid_refs.append(ref)
                reasons.append(f"evidence_{record.status.value}:{ref}")
                continue
            fact_reason = _validate_claim_fact(claim, record)
            if fact_reason:
                invalid_refs.append(ref)
                reasons.append(f"{fact_reason}:{ref}")
                continue
            valid_refs.append(ref)
            if record.status == EvidenceStatus.SYNTHETIC or record.data_mode == "synthetic":
                reasons.append(f"synthetic_evidence:{ref}")

        if any(reason.startswith("evidence_conflicting") for reason in reasons):
            status = "conflicting"
        elif not valid_refs or invalid_refs:
            status = "unsupported"
        elif any(reason.startswith("synthetic_evidence") for reason in reasons):
            status = "partial"
        else:
            status = "supported"
        validated = ValidatedClaim(
            **claim.model_dump(),
            validation_status=status,
            validation_reasons=list(dict.fromkeys(reasons)),
        )
        return ClaimValidationResult(
            claim=validated,
            valid_evidence_refs=valid_refs,
            invalid_evidence_refs=invalid_refs,
        )

    def coverage(
        self,
        required_dimensions: list[str],
        required_items: list[dict[str, str]] | None = None,
    ) -> EvidenceCoverage:
        required = list(dict.fromkeys(required_dimensions))
        if required_items:
            normalized_items = [
                {
                    "entity_id": str(item["entity_id"]),
                    "dimension": str(item["dimension"]),
                    "fact_path": str(item.get("fact_path") or ""),
                }
                for item in required_items
                if item.get("entity_id") and item.get("dimension")
            ]
            covered_items = [
                _coverage_item_key(item)
                for item in normalized_items
                if _has_usable_evidence(
                    self.records,
                    entity_id=item["entity_id"],
                    dimension=item["dimension"],
                    fact_path=item["fact_path"] or None,
                )
            ]
            all_items = [_coverage_item_key(item) for item in normalized_items]
            missing_items = [item for item in all_items if item not in covered_items]
            covered = [
                dimension
                for dimension in required
                if any(
                    item["dimension"] == dimension
                    and _coverage_item_key(item) in covered_items
                    for item in normalized_items
                )
            ]
            missing = [dimension for dimension in required if dimension not in covered]
            return EvidenceCoverage(
                required_dimensions=required,
                covered_dimensions=covered,
                missing_dimensions=missing,
                required_items=all_items,
                covered_items=covered_items,
                missing_items=missing_items,
                coverage_ratio=len(covered_items) / len(all_items) if all_items else 1.0,
            )
        covered = [
            dimension
            for dimension in required
            if any(
                record.dimension == dimension
                and record.status in {EvidenceStatus.AVAILABLE, EvidenceStatus.CONFIRMED_EMPTY}
                and record.data_mode == "formal"
                for record in self.records
            )
        ]
        missing = [dimension for dimension in required if dimension not in covered]
        return EvidenceCoverage(
            required_dimensions=required,
            covered_dimensions=covered,
            missing_dimensions=missing,
            required_items=[],
            covered_items=[],
            missing_items=[],
            coverage_ratio=len(covered) / len(required) if required else 1.0,
        )


_MISSING = object()


def _coverage_item_key(item: dict[str, str]) -> str:
    return ":".join((item["entity_id"], item["dimension"], item.get("fact_path") or "*"))


def _has_usable_evidence(
    records: list[EvidenceRecord],
    *,
    entity_id: str,
    dimension: str,
    fact_path: str | None,
) -> bool:
    for record in records:
        if record.entity_id != entity_id or record.dimension != dimension:
            continue
        if record.status not in {EvidenceStatus.AVAILABLE, EvidenceStatus.CONFIRMED_EMPTY}:
            continue
        if record.data_mode == "synthetic":
            continue
        if fact_path and _read_fact(record.facts, fact_path) is _MISSING:
            continue
        return True
    return False


def _read_fact(facts: dict[str, Any], path: str) -> Any:
    current: Any = facts
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return _MISSING
    return current


def _validate_claim_fact(claim: Claim, record: EvidenceRecord) -> str | None:
    """Compare a structured claim with the exact fact in its evidence."""
    if not claim.fact_path:
        # Compatibility for historical graph claims. New deterministic claims
        # must provide fact_path and are checked by the branch below.
        if claim.value is None or _contains_equal_value(record.facts, claim.value):
            return None
        return "claim_value_not_supported"

    actual = _read_fact(record.facts, claim.fact_path)
    if actual is _MISSING:
        return "fact_not_found"
    if claim.unit:
        observed_unit = record.facts.get(f"{claim.fact_path}_unit") or record.facts.get("unit")
        if not observed_unit:
            return "evidence_unit_missing"
        if str(observed_unit).strip().casefold() != claim.unit.strip().casefold():
            return "unit_mismatch"
    if claim.operator == "exists":
        return None if actual is not None else "fact_not_found"
    if claim.operator == "contains":
        if isinstance(actual, str):
            return None if str(claim.value) in actual else "claim_value_mismatch"
        if isinstance(actual, (list, tuple, set)):
            return None if claim.value in actual else "claim_value_mismatch"
        return "claim_operator_invalid"
    try:
        matches = {
            "eq": _safe_equal(actual, claim.value),
            "neq": not _safe_equal(actual, claim.value),
            "gt": actual > claim.value,
            "gte": actual >= claim.value,
            "lt": actual < claim.value,
            "lte": actual <= claim.value,
        }[claim.operator]
    except (KeyError, TypeError, ValueError):
        return "claim_operator_invalid"
    return None if matches else "claim_value_mismatch"


def _safe_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str) or isinstance(expected, str):
        return str(actual).strip().casefold() == str(expected).strip().casefold()
    return actual == expected


def _contains_equal_value(value: Any, expected: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_equal_value(item, expected) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_equal_value(item, expected) for item in value)
    return _safe_equal(value, expected)


def build_evidence_record(
    *,
    evidence_id: str,
    entity_id: str,
    dimension: str,
    provider: str,
    source_type: str,
    payload: dict[str, Any],
    status: EvidenceStatus = EvidenceStatus.AVAILABLE,
    collected_at: datetime | None = None,
    valid_until: datetime | None = None,
    data_mode: str = "formal",
    endpoint: str | None = None,
    query: dict[str, Any] | None = None,
    raw_payload_ref: str | None = None,
) -> EvidenceRecord:
    """Normalize provider payload and derive a stable content hash."""
    content_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return EvidenceRecord(
        evidence_id=evidence_id,
        entity_id=entity_id,
        dimension=dimension,
        provider=provider,
        source_type=source_type,
        endpoint=endpoint,
        query=query or {},
        status=status,
        collected_at=collected_at or datetime.now(timezone.utc),
        valid_until=valid_until,
        data_mode=data_mode,
        content_hash=content_hash,
        raw_payload_ref=raw_payload_ref,
        facts=payload,
    )


__all__ = [
    "Claim", "ClaimValidationResult", "EvidenceCoverage", "EvidenceLedger", "EvidenceRecord",
    "EvidenceStatus", "ValidatedClaim", "build_evidence_record",
]
