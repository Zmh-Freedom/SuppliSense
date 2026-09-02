"""Evidence normalization and Claim-Evidence validation for Agent answers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

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

    def coverage(self, required_dimensions: list[str]) -> EvidenceCoverage:
        required = list(dict.fromkeys(required_dimensions))
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
            coverage_ratio=len(covered) / len(required) if required else 1.0,
        )


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
