"""Current supplier compliance read model, separate from the legacy profile."""

from datetime import datetime, timezone
from typing import Any

from app.db.mongo import get_db
from app.domains.risk.assessment_evidence import provider_receipt
from app.domains.risk.evidence_validity import evaluate_evidence_validity


DOMESTIC_SOURCES = (
    ("lawSuit", "lawsuit"),
    ("courtRegister", "court_register"),
    ("executedPerson", "executed_person"),
    ("dishonesty", "dishonesty"),
    ("abnormal", "abnormal_operation"),
    ("punishmentInfo", "administrative_penalty"),
    ("taxArrears", "tax_arrears"),
)


def _utc_now(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _unknown_validity(source: str, reason: str, *, query_receipt: dict | None = None) -> dict:
    return {
        "source": source,
        "status": "unknown",
        "reason_codes": [reason],
        "detail": "来源缺少可验证的主体、版本或时间信息，当前不能确认该事实。",
        "query_receipt": query_receipt or {},
    }


def _missing_validity(source: str) -> dict:
    return {
        "source": source,
        "status": "missing",
        "reason_codes": ["SOURCE_MISSING"],
        "detail": "当前没有该来源记录。",
        "query_receipt": {"status": "unavailable", "reason": "not_queried"},
    }


def _read_one_source(
    db: Any,
    collection_name: str,
    kind: str,
    *,
    company_name: str,
    entity_id: str,
    scope: str,
    now: datetime,
) -> dict:
    try:
        document = db[collection_name].find_one({"name": company_name})
    except Exception:
        document = None
        read_failed = True
    else:
        read_failed = False

    if read_failed:
        return {
            "kind": kind,
            "count": None,
            "validity": {
                "source": collection_name,
                "status": "failed",
                "reason_codes": ["SOURCE_QUERY_FAILED"],
                "detail": "来源读取失败，当前无法核验。",
                "query_receipt": {"status": "failed"},
            },
            "source": collection_name,
        }
    if not isinstance(document, dict):
        return {
            "kind": kind,
            "count": None,
            "validity": _missing_validity(collection_name),
            "source": collection_name,
        }

    receipt = provider_receipt(document)
    receipt_ok = receipt.get("status") == "success"
    count = receipt.get("record_count") if receipt_ok else None
    source_entity_id = document.get("entity_id") or document.get("company_id")
    source_scope = document.get("scope")
    has_identity_binding = bool(source_entity_id and source_scope)
    if not receipt_ok:
        source_status = "failed" if receipt.get("reason") == "provider_failed" else "unknown"
    elif not has_identity_binding:
        source_status = "unknown"
        count = None
    else:
        source_status = "empty" if count == 0 else "available"

    evidence = {
        "source": collection_name,
        "source_version": document.get("source_version") or document.get("version"),
        "entity_id": source_entity_id,
        "scope": source_scope,
        "source_mode": document.get("source_mode"),
        "status": source_status,
        "observed_at": document.get("source_observed_at") or document.get("observed_at"),
        "collected_at": document.get("source_collected_at") or document.get("collected_at"),
        "expires_at": document.get("source_expires_at") or document.get("expires_at"),
        "query_receipt": receipt,
        "coverage_partial": bool(document.get("coverage_partial")),
        "conflict": bool(document.get("conflict")),
    }
    validity = evaluate_evidence_validity(
        evidence,
        expected_entity_id=entity_id,
        expected_scope=scope,
        now=now,
    )
    if validity.get("status") not in {"available", "empty", "stale"}:
        count = None
    return {
        "kind": kind,
        "count": count,
        "validity": validity,
        "source": collection_name,
    }


def read_domestic_findings(
    company_name: str,
    *,
    entity_id: str,
    scope: str,
    now: datetime,
) -> list[dict]:
    """Read domestic risk facts without treating missing data as a zero count."""
    checked_at = _utc_now(now)
    if not entity_id or not scope:
        return [
            {
                "kind": kind,
                "count": None,
                "validity": _unknown_validity(source, "IDENTITY_SCOPE_REQUIRED"),
                "source": source,
            }
            for source, kind in DOMESTIC_SOURCES
        ]

    try:
        db = get_db()
    except Exception:
        return [
            {
                "kind": kind,
                "count": None,
                "validity": {
                    "source": source,
                    "status": "failed",
                    "reason_codes": ["SOURCE_QUERY_FAILED"],
                    "detail": "国内来源数据库不可用，当前无法核验。",
                    "query_receipt": {"status": "failed"},
                },
                "source": source,
            }
            for source, kind in DOMESTIC_SOURCES
        ]
    return [
        _read_one_source(
            db,
            source,
            kind,
            company_name=company_name,
            entity_id=entity_id,
            scope=scope,
            now=checked_at,
        )
        for source, kind in DOMESTIC_SOURCES
    ]


def build_supplier_compliance_status(
    supplier_id: str,
    *,
    now: datetime | None = None,
) -> dict:
    """Build the versioned compliance view from one screening read."""
    from app.domains.risk.sanctions_service import analyze_sanctions
    from app.domains.supplier.repo import get_supplier

    supplier = get_supplier(supplier_id)
    if not supplier:
        raise ValueError(f"Supplier {supplier_id} not found")

    screening = analyze_sanctions(str(supplier.get("name") or ""), now=now)
    domestic_findings = screening.get("domestic_findings") or []
    limitations = list(screening.get("limitations") or [])
    if any(item.get("validity", {}).get("status") in {"unknown", "missing", "failed", "stale", "conflicting"} for item in domestic_findings):
        limitations.append("部分国内司法或经营来源缺失、过期或无法核验。")
    return {
        "schema_version": 1,
        "screening": screening,
        "domestic_findings": domestic_findings,
        "limitations": list(dict.fromkeys(limitations)),
    }
