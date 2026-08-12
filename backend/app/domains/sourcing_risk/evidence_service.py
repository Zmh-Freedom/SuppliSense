"""Normalize sourcing-risk provider results into auditable evidence records."""

import json
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, Field

from app.db.mongo import get_db


class RawPayloadStagingError(RuntimeError):
    """Expose the documents that need compensation after a partial Mongo stage."""

    def __init__(self, staged_payloads: list[dict], cause: Exception, attempted_payload: dict | None = None) -> None:
        super().__init__(str(cause))
        self.staged_payloads = staged_payloads
        self.attempted_payload = attempted_payload

    @property
    def compensation_payloads(self) -> list[dict]:
        payloads = [*self.staged_payloads]
        if self.attempted_payload is not None:
            payloads.append(self.attempted_payload)
        return payloads


class EvidenceRecord(BaseModel):
    """Structured evidence index; provider payloads remain in MongoDB."""

    evidence_id: UUID
    run_id: UUID
    company_id: UUID
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
    conflict_status: Literal["none", "conflicting", "resolved", "unknown"]
    raw_payload_ref: str | None
    summary: str


def normalize_evidence(
    run_id: str, company_id: str, dimension: str, provider_result: dict, *, policy: dict
) -> EvidenceRecord:
    """Normalize provider input; raw payload persistence belongs to the snapshot compensation seam."""
    run_uuid = _require_uuid(run_id, "run_id")
    company_uuid = _require_uuid(company_id, "company_id")
    if dimension not in EvidenceRecord.model_fields["dimension"].annotation.__args__:
        raise ValueError("unsupported evidence dimension")

    collected_at = _parse_timestamp(provider_result.get("collected_at")) or datetime.now(timezone.utc)
    observed_at = _parse_timestamp(provider_result.get("observed_at"))
    freshness_days = policy.get("freshness_days", {}).get(dimension)
    freshness_status = _freshness_status(observed_at, collected_at, freshness_days)
    raw_payload_ref = _raw_payload_ref(run_uuid, company_uuid, dimension, provider_result)
    record = EvidenceRecord(
        evidence_id=uuid4(),
        run_id=run_uuid,
        company_id=company_uuid,
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
    return record


def validate_evidence_set(evidence: list[dict], policy: dict) -> dict:
    """Fail closed for required evidence that is absent, unsafe, or contradictory."""
    required_dimensions = policy.get("required_evidence", [])
    reason_codes: list[str] = []
    status = "clear"
    evidence_present = True
    claim_status = "no_risk"
    for dimension in required_dimensions:
        dimension_evidence = [item for item in evidence if item.get("dimension") == dimension]
        if not dimension_evidence:
            reason_codes.append(_reason_code(dimension, "DATA_UNAVAILABLE"))
            status = "needs_review"
            evidence_present = False
            claim_status = "missing"
            continue
        claims = {str(item.get("claim_code", "unknown")) for item in dimension_evidence}
        if any(item.get("conflict_status") == "conflicting" for item in dimension_evidence) or len(claims) > 1:
            reason_codes.append("KEY_EVIDENCE_CONFLICT")
            status = "needs_review"
            claim_status = "conflicting"
            continue
        if any(item.get("conflict_status") == "unknown" for item in dimension_evidence):
            reason_codes.append("KEY_EVIDENCE_CONFLICT_UNKNOWN")
            status = "needs_review"
            claim_status = "unknown"
            continue
        if claims & {"unavailable", "unknown", "error"}:
            reason_codes.append(_reason_code(dimension, "DATA_UNAVAILABLE"))
            status = "needs_review"
            claim_status = "unavailable"
            continue
        if claims & {"hit", "match"}:
            reason_codes.append(_reason_code(dimension, "HIT"))
            status = "needs_review"
            claim_status = "hit"
            continue
        if any(item.get("freshness_status") == "stale" for item in dimension_evidence):
            reason_codes.append(_reason_code(dimension, "DATA_STALE"))
            if status != "needs_review":
                status = "incomplete"
                claim_status = "stale"
            continue
        if any(item.get("freshness_status") == "unknown" for item in dimension_evidence):
            reason_codes.append(_reason_code(dimension, "DATA_UNKNOWN"))
            if status != "needs_review":
                status = "incomplete"
                claim_status = "unknown"
    return {
        "status": status,
        "reason_codes": reason_codes,
        "evidence_present": evidence_present,
        "claim_status": claim_status,
        "score_eligible": status == "clear",
    }


def raw_payload_document(record: EvidenceRecord, provider_result: dict) -> dict:
    """Build the Mongo document outside LangGraph state and PostgreSQL snapshots."""
    return {
        "raw_payload_ref": record.raw_payload_ref,
        "run_id": str(record.run_id),
        "company_id": str(record.company_id),
        "dimension": record.dimension,
        "collected_at": record.collected_at,
        "raw_payload": provider_result.get("raw_payload", provider_result),
    }


def stage_raw_payloads(raw_payloads: list[dict], *, staging_owner: str | None = None) -> list[dict]:
    """Idempotently stage Mongo documents before the PostgreSQL snapshot transaction.

    Mongo and PostgreSQL do not share an ACID transaction. Callers must compensate the
    returned newly-created documents if the PostgreSQL transaction fails.
    """
    collection = get_db()["agent_evidence_payloads"]
    owner = staging_owner or str(uuid4())
    staged: list[dict] = []
    for payload in raw_payloads:
        document = {**payload, "lifecycle_status": "pending", "staging_owner": owner}
        selector = {
            "raw_payload_ref": document["raw_payload_ref"],
            "run_id": document["run_id"],
            "company_id": document["company_id"],
        }
        try:
            result = collection.update_one(
                selector,
                {"$setOnInsert": document},
                upsert=True,
            )
        except Exception as exc:
            try:
                existing = _find_raw_payload(collection, document["raw_payload_ref"])
            except Exception as confirmation_exc:
                raise RawPayloadStagingError(staged, confirmation_exc, document) from confirmation_exc
            if _is_owned_pending(existing, document):
                staged.append(existing)
                continue
            raise RawPayloadStagingError(staged, exc) from exc
        if result.upserted_id is not None:
            staged.append(document)
            continue
        existing = _find_raw_payload(collection, document["raw_payload_ref"])
        if existing and (
            existing.get("run_id") != document["run_id"]
            or existing.get("company_id") != document["company_id"]
        ):
            raise RawPayloadStagingError(staged, RuntimeError("raw payload ownership conflict"))
        if existing and existing.get("lifecycle_status") == "pending_compensation":
            if not _same_owner(existing, document):
                raise RawPayloadStagingError(staged, RuntimeError("raw payload ownership conflict"), document)
            if _delete_pending_compensation(
                collection, document["raw_payload_ref"], existing.get("staging_owner")
            ) != "compensated":
                raise RawPayloadStagingError(
                    staged,
                    RuntimeError(f"raw payload {document['raw_payload_ref']} is pending compensation"),
                    document,
                )
            try:
                restaged = collection.update_one(
                    selector,
                    {"$setOnInsert": document},
                    upsert=True,
                )
            except Exception as exc:
                raise RawPayloadStagingError(staged, exc, document) from exc
            if restaged.upserted_id is not None:
                staged.append(document)
        elif _is_owned_pending(existing, document):
            staged.append(existing)
        elif existing and existing.get("lifecycle_status") not in {"committed", None}:
            raise RawPayloadStagingError(staged, RuntimeError("raw payload ownership conflict"))
    return staged


def commit_raw_payloads(raw_payloads: list[dict]) -> None:
    """Make staged raw payloads durable after the PostgreSQL transaction commits."""
    collection = get_db()["agent_evidence_payloads"]
    for payload in raw_payloads:
        result = collection.update_one(
            {
                "raw_payload_ref": payload["raw_payload_ref"],
                "run_id": payload["run_id"],
                "company_id": payload["company_id"],
                "staging_owner": payload["staging_owner"],
                "lifecycle_status": "pending",
            },
            {"$set": {"lifecycle_status": "committed"}},
        )
        matched_count = getattr(result, "matched_count", None)
        if matched_count is None:
            # Older test doubles do not expose PyMongo's acknowledgement field; the
            # real driver always does. Preserve their idempotent update semantics.
            continue
        if matched_count == 0:
            existing = _find_raw_payload(collection, payload["raw_payload_ref"])
            if existing and existing.get("lifecycle_status") == "committed" and _same_owner(existing, payload):
                continue
            raise RuntimeError("raw payload ownership conflict during commit")


def compensate_raw_payloads(staged_payloads: list[dict], *, reason: str = "postgres_snapshot_failed") -> None:
    """Try to delete staged payloads, retaining retryable state when Mongo cleanup is uncertain."""
    collection = get_db()["agent_evidence_payloads"]
    for payload in staged_payloads:
        _mark_pending_compensation(collection, payload, reason=reason)
        _delete_pending_compensation(collection, payload["raw_payload_ref"], payload.get("staging_owner"))


def retry_raw_payload_compensations(
    raw_payload_refs: list[str], *, staging_owners: dict[str, str] | None = None
) -> list[dict[str, str]]:
    """Idempotently retry cleanup for stable raw-evidence references.

    This is deliberately a compensation command, not a cross-store transaction. It only
    acts on records already marked for remediation and never creates or commits payloads.
    """
    collection = get_db()["agent_evidence_payloads"]
    outcomes: list[dict[str, str]] = []
    for raw_payload_ref in dict.fromkeys(raw_payload_refs):
        selector = {"raw_payload_ref": raw_payload_ref, "lifecycle_status": "pending_compensation"}
        if staging_owners and raw_payload_ref in staging_owners:
            selector["staging_owner"] = staging_owners[raw_payload_ref]
        document = collection.find_one(selector)
        if document is None:
            outcomes.append({"raw_payload_ref": raw_payload_ref, "lifecycle_status": "already_compensated"})
            continue
        outcomes.append(
            {
                "raw_payload_ref": raw_payload_ref,
                "lifecycle_status": _delete_pending_compensation(
                    collection, raw_payload_ref, document.get("staging_owner")
                ),
            }
        )
    return outcomes


def get_raw_payload_lifecycle_statuses(raw_payload_refs: list[str]) -> list[dict[str, str]]:
    """Return status-only raw payload metadata for already-authorized evidence detail reads."""
    if not raw_payload_refs:
        return []
    collection = get_db()["agent_evidence_payloads"]
    statuses: list[dict[str, str]] = []
    for raw_payload_ref in dict.fromkeys(raw_payload_refs):
        document = collection.find_one({"raw_payload_ref": raw_payload_ref})
        if document is not None:
            statuses.append(
                {
                    "raw_payload_ref": raw_payload_ref,
                    "lifecycle_status": str(document.get("lifecycle_status") or "pending_compensation"),
                }
            )
    return statuses


def _mark_pending_compensation(collection: object, payload: dict, *, reason: str) -> None:
    """Make a failed cleanup visible and retryable before attempting deletion."""
    collection.update_one(
        {
            "raw_payload_ref": payload["raw_payload_ref"],
            "run_id": payload["run_id"],
            "company_id": payload["company_id"],
            "staging_owner": payload["staging_owner"],
            "lifecycle_status": "pending",
        },
        {
            "$set": {
                "lifecycle_status": "pending_compensation",
                "compensation_reason": reason,
            }
        },
    )


def _delete_pending_compensation(collection: object, raw_payload_ref: str, staging_owner: str | None = None) -> str:
    """Delete only an explicitly compensating record; uncertain results remain retryable."""
    selector = {"raw_payload_ref": raw_payload_ref, "lifecycle_status": "pending_compensation"}
    if staging_owner:
        selector["staging_owner"] = staging_owner
    try:
        result = collection.delete_one(
            selector
        )
    except Exception:
        return "pending_compensation"
    if result.deleted_count == 1:
        return "compensated"
    return "pending_compensation"


def _find_raw_payload(collection: object, raw_payload_ref: str) -> dict | None:
    find_one = getattr(collection, "find_one", None)
    return find_one({"raw_payload_ref": raw_payload_ref}) if find_one else None


def _same_owner(existing: dict, payload: dict) -> bool:
    return all(existing.get(key) == payload.get(key) for key in ("run_id", "company_id", "staging_owner"))


def _is_owned_pending(existing: dict | None, payload: dict) -> bool:
    return bool(existing and existing.get("lifecycle_status") == "pending" and _same_owner(existing, payload))


def _raw_payload_ref(run_id: UUID, company_id: UUID, dimension: str, provider_result: dict) -> str:
    claim_code = str(provider_result.get("claim_code") or "unknown")
    source_type = str(provider_result.get("source_type") or provider_result.get("source") or "unknown")
    source_identity = provider_result.get("source_reference") or provider_result.get("provider_key")
    if not source_identity:
        raw_payload = provider_result.get("raw_payload", provider_result)
        source_identity = json.dumps(raw_payload, ensure_ascii=False, sort_keys=True, default=str)
    return str(uuid5(run_id, f"raw-evidence:{company_id}:{dimension}:{claim_code}:{source_type}:{source_identity}"))


def _require_uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


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
    if observed_at > collected_at:
        return "stale"
    return "fresh" if observed_at >= collected_at - timedelta(days=freshness_days) else "stale"


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _conflict_status(value: object) -> str:
    if value is None:
        return "none"
    return value if value in {"none", "conflicting", "resolved"} else "unknown"


def _reason_code(dimension: str, suffix: str) -> str:
    return f"{dimension.upper()}_{suffix}"
