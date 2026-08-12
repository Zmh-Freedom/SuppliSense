"""Normalize sourcing-risk provider results into auditable evidence records."""

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.db.mongo import get_db
from app.domains.agent_run import repo as agent_run_repo


class EvidenceRecord(BaseModel):
    """Structured evidence index; provider payloads remain in MongoDB."""

    evidence_id: str
    run_id: str
    company_id: str
    dimension: Literal[
        "company", "financial", "judicial", "sentiment", "sanctions", "esg", "continuity"
    ]
    claim_code: str
    source_type: str
    source_reference: str | None
    observed_at: datetime
    collected_at: datetime
    confidence: float = Field(ge=0, le=1)
    freshness_status: Literal["fresh", "stale", "unknown"]
    conflict_status: Literal["none", "conflicting", "resolved"]
    raw_payload_ref: str | None
    summary: str


def normalize_evidence(
    run_id: str, company_id: str, dimension: str, provider_result: dict
) -> EvidenceRecord:
    """Persist raw input separately and return its normalized structured index."""
    if dimension not in EvidenceRecord.model_fields["dimension"].annotation.__args__:
        raise ValueError("unsupported evidence dimension")

    collected_at = _parse_timestamp(provider_result.get("collected_at")) or datetime.now(timezone.utc)
    observed_at = _parse_timestamp(provider_result.get("observed_at"))
    freshness_days = provider_result.get("freshness_days")
    freshness_status = _freshness_status(observed_at, collected_at, freshness_days)
    raw_payload_ref = _persist_raw_payload(run_id, company_id, dimension, provider_result, collected_at)
    record = EvidenceRecord(
        evidence_id=str(uuid4()),
        run_id=run_id,
        company_id=company_id,
        dimension=dimension,
        claim_code=str(provider_result.get("claim_code") or "unknown"),
        source_type=str(provider_result.get("source_type") or "unknown"),
        source_reference=provider_result.get("source_reference"),
        observed_at=observed_at or collected_at,
        collected_at=collected_at,
        confidence=_confidence(provider_result.get("confidence")),
        freshness_status=freshness_status,
        conflict_status=_conflict_status(provider_result.get("conflict_status")),
        raw_payload_ref=raw_payload_ref,
        summary=str(provider_result.get("summary") or ""),
    )
    agent_run_repo.insert_evidence(
        run_id=run_id,
        candidate_id=company_id,
        evidence_type=dimension,
        source=record.source_type,
        source_reference=record.source_reference,
        evidence_snapshot=record.model_dump(mode="json"),
    )
    return record


def validate_evidence_set(evidence: list[dict], policy: dict) -> dict:
    """Fail closed for required evidence that is absent, unsafe, or contradictory."""
    required_dimensions = policy.get("required_evidence", [])
    reason_codes: list[str] = []
    status = "clear"
    for dimension in required_dimensions:
        dimension_evidence = [item for item in evidence if item.get("dimension") == dimension]
        if not dimension_evidence:
            reason_codes.append(_reason_code(dimension, "DATA_UNAVAILABLE"))
            status = "needs_review"
            continue
        claims = {str(item.get("claim_code", "unknown")) for item in dimension_evidence}
        if any(item.get("conflict_status") == "conflicting" for item in dimension_evidence) or len(claims) > 1:
            reason_codes.append("KEY_EVIDENCE_CONFLICT")
            status = "needs_review"
            continue
        if claims & {"unavailable", "unknown", "error"}:
            reason_codes.append(_reason_code(dimension, "DATA_UNAVAILABLE"))
            status = "needs_review"
            continue
        if claims & {"hit", "match"}:
            reason_codes.append(_reason_code(dimension, "HIT"))
            status = "needs_review"
            continue
        if any(item.get("freshness_status") == "stale" for item in dimension_evidence):
            reason_codes.append(_reason_code(dimension, "DATA_STALE"))
            if status != "needs_review":
                status = "incomplete"
            continue
        if any(item.get("freshness_status") == "unknown" for item in dimension_evidence):
            reason_codes.append(_reason_code(dimension, "DATA_UNKNOWN"))
            if status != "needs_review":
                status = "incomplete"
    return {"status": status, "reason_codes": reason_codes}


def _persist_raw_payload(
    run_id: str, company_id: str, dimension: str, provider_result: dict, collected_at: datetime
) -> str:
    raw_payload_ref = str(uuid4())
    raw_payload = provider_result.get("raw_payload", provider_result)
    get_db()["agent_evidence_payloads"].insert_one(
        {
            "raw_payload_ref": raw_payload_ref,
            "run_id": run_id,
            "company_id": company_id,
            "dimension": dimension,
            "collected_at": collected_at,
            "raw_payload": raw_payload,
        }
    )
    return raw_payload_ref


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _freshness_status(observed_at: datetime | None, collected_at: datetime, freshness_days: object) -> str:
    if observed_at is None or isinstance(freshness_days, bool) or not isinstance(freshness_days, int) or freshness_days < 1:
        return "unknown"
    return "fresh" if observed_at >= collected_at - timedelta(days=freshness_days) else "stale"


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _conflict_status(value: object) -> str:
    return value if value in {"none", "conflicting", "resolved"} else "none"


def _reason_code(dimension: str, suffix: str) -> str:
    return f"{dimension.upper()}_{suffix}"
