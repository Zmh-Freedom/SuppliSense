"""Framework-independent Company Identity read and resolution services."""

from psycopg2.errors import UniqueViolation

from app.core.errors import DomainError
from app.db.postgres import get_cursor
from app.domains.company import repo as company_repo
from app.domains.company.normalization import normalize_company_name, normalize_credit_code
from app.domains.auth.audit_repo import create_log_with_cursor
from app.domains.outbox.repo import enqueue_event
from app.schemas.company import CompanyCreateInput, CompanyUpdateInput, CompanyVerifyInput


MAX_MERGE_HOPS = 20
_ALIAS_EXACT_CONFIDENCE = 0.95
_TRUSTED_IDENTITY_SOURCES = {"tianyancha", "import", "admin_verified"}
_WRITER_ROLES = {"admin", "analyst"}
_ANALYST_RESTRICTED_UPDATE_FIELDS = {
    "unified_social_credit_code",
    "identity_source",
    "source_reference",
}


def create_company(
    data: CompanyCreateInput,
    actor_id: str | None,
    actor_role: str,
) -> dict:
    """Create a company, audit record, and outbox event in one PostgreSQL transaction."""
    _require_writer_role(actor_role)
    verification_status = _enum_value(data.verification_status)
    if verification_status == "verified" and actor_role != "admin":
        raise _verification_forbidden_error()

    credit_code = normalize_credit_code(data.unified_social_credit_code)
    if verification_status == "verified":
        _require_verification_evidence(credit_code, data.identity_source, data.source_reference)

    try:
        with get_cursor() as (_, cur):
            _raise_if_credit_code_conflicts(cur, credit_code)
            company = company_repo.insert_company(
                cur,
                legal_name=data.legal_name,
                normalized_name=normalize_company_name(data.legal_name),
                unified_social_credit_code=credit_code,
                registration_status=data.registration_status,
                verification_status=verification_status,
                identity_source=data.identity_source,
                source_reference=data.source_reference,
                created_by=actor_id,
                verified_by=actor_id if verification_status == "verified" else None,
            )
            for alias in data.aliases:
                company_repo.insert_alias(
                    cur,
                    company_id=company["id"],
                    alias_name=alias.alias_name,
                    normalized_alias=normalize_company_name(alias.alias_name),
                    alias_type=alias.alias_type,
                    source=alias.source,
                    confidence=alias.confidence,
                    created_by=actor_id,
                )
            _write_audit_and_event(cur, company, "created", actor_id)
    except UniqueViolation:
        _raise_credit_code_conflict(credit_code)
    return _command_result(company)


def update_company(
    company_id: str,
    data: CompanyUpdateInput,
    actor_id: str | None,
    actor_role: str,
) -> dict:
    """Optimistically update a canonical company and write its audit/event atomically."""
    _require_writer_role(actor_role)
    specified_fields = set(data.model_fields_set)
    if actor_role != "admin" and specified_fields & _ANALYST_RESTRICTED_UPDATE_FIELDS:
        raise DomainError(
            "COMPANY_IDENTITY_FIELD_FORBIDDEN",
            "分析师不能修改权威身份字段",
            403,
        )

    try:
        with get_cursor() as (_, cur):
            current = _require_current_company(cur, company_id)
            changes = _company_update_changes(data, specified_fields)
            prospective_credit_code = changes.get(
                "unified_social_credit_code", current["unified_social_credit_code"]
            )
            prospective_source = changes.get("identity_source", current["identity_source"])
            prospective_reference = changes.get("source_reference", current["source_reference"])
            if current["verification_status"] == "verified":
                _require_verification_evidence(
                    prospective_credit_code,
                    prospective_source,
                    prospective_reference,
                )
            if "unified_social_credit_code" in changes:
                _raise_if_credit_code_conflicts(cur, prospective_credit_code, company_id)
            company = company_repo.update_company(cur, company_id, data.expected_version, changes)
            if company is None:
                _raise_update_failure(cur, company_id)
            _write_audit_and_event(cur, company, "updated", actor_id)
    except UniqueViolation:
        _raise_credit_code_conflict(
            normalize_credit_code(data.unified_social_credit_code)
            if "unified_social_credit_code" in specified_fields
            else None
        )
    return _command_result(company)


def verify_company(
    company_id: str,
    data: CompanyVerifyInput,
    actor_id: str | None,
    actor_role: str,
) -> dict:
    """Verify a company as an administrator and atomically record its domain event."""
    if actor_role != "admin":
        raise _verification_forbidden_error()

    try:
        with get_cursor() as (_, cur):
            current = _require_current_company(cur, company_id)
            credit_code = (
                normalize_credit_code(data.unified_social_credit_code)
                if data.unified_social_credit_code is not None
                else current["unified_social_credit_code"]
            )
            _require_verification_evidence(
                credit_code,
                data.identity_source,
                data.source_reference,
            )
            _raise_if_credit_code_conflicts(cur, credit_code, company_id)
            company = company_repo.verify_company(
                cur,
                company_id,
                data.expected_version,
                credit_code,
                data.identity_source,
                data.source_reference,
                actor_id,
            )
            if company is None:
                _raise_update_failure(cur, company_id)
            _write_audit_and_event(cur, company, "verified", actor_id)
    except UniqueViolation:
        _raise_credit_code_conflict(
            normalize_credit_code(data.unified_social_credit_code)
            if data.unified_social_credit_code is not None
            else None
        )
    return _command_result(company)


def _require_writer_role(actor_role: str) -> None:
    if actor_role not in _WRITER_ROLES:
        raise DomainError("COMPANY_WRITE_FORBIDDEN", "没有企业写入权限", 403)


def _verification_forbidden_error() -> DomainError:
    return DomainError("COMPANY_VERIFICATION_FORBIDDEN", "只有管理员可以核验企业", 403)


def _require_verification_evidence(
    credit_code: str | None,
    identity_source: str,
    source_reference: str | None,
) -> None:
    if credit_code is not None:
        return
    if identity_source in _TRUSTED_IDENTITY_SOURCES and source_reference and source_reference.strip():
        return
    raise DomainError(
        "COMPANY_VERIFICATION_EVIDENCE_REQUIRED",
        "已核验企业必须具有有效统一社会信用代码或可信来源引用",
        422,
    )


def _raise_if_credit_code_conflicts(
    cur: object,
    credit_code: str | None,
    company_id: str | None = None,
) -> None:
    if credit_code is None:
        return
    conflicting_company = company_repo.find_by_credit_code_with_cursor(cur, credit_code)
    if conflicting_company is not None and conflicting_company["id"] != company_id:
        raise DomainError(
            "COMPANY_ALREADY_EXISTS",
            "统一社会信用代码已存在",
            409,
            {"company_id": conflicting_company["id"]},
        )


def _raise_credit_code_conflict(credit_code: str | None) -> None:
    conflicting_company = None
    if credit_code is not None:
        with get_cursor() as (_, cur):
            conflicting_company = company_repo.find_by_credit_code_with_cursor(cur, credit_code)
    raise DomainError(
        "COMPANY_ALREADY_EXISTS",
        "统一社会信用代码已存在",
        409,
        {"company_id": conflicting_company["id"]} if conflicting_company is not None else None,
    )


def _require_current_company(cur: object, company_id: str) -> dict:
    company = company_repo.get_company_row_with_cursor(cur, company_id)
    if company is None:
        raise DomainError("COMPANY_NOT_FOUND", "企业不存在", 404)
    if company["merged_into_id"] is not None:
        raise DomainError("COMPANY_MERGED_SUBJECT", "已合并企业不能修改", 409)
    return company


def _company_update_changes(
    data: CompanyUpdateInput,
    specified_fields: set[str],
) -> dict[str, object]:
    changes: dict[str, object] = {}
    if "legal_name" in specified_fields:
        changes["legal_name"] = data.legal_name
        changes["normalized_name"] = normalize_company_name(data.legal_name or "")
    if "unified_social_credit_code" in specified_fields:
        changes["unified_social_credit_code"] = normalize_credit_code(
            data.unified_social_credit_code
        )
    for field_name in ("registration_status", "identity_source", "source_reference"):
        if field_name in specified_fields:
            changes[field_name] = getattr(data, field_name)
    return changes


def _raise_update_failure(cur: object, company_id: str) -> None:
    current = company_repo.get_company_row_with_cursor(cur, company_id)
    if current is not None and current["merged_into_id"] is not None:
        raise DomainError("COMPANY_MERGED_SUBJECT", "已合并企业不能修改", 409)
    raise DomainError("COMPANY_VERSION_CONFLICT", "企业身份版本已变更", 409)


def _write_audit_and_event(
    cur: object,
    company: dict,
    action: str,
    actor_id: str | None,
) -> None:
    event_payload = {
        "company_id": company["id"],
        "legal_name": company["legal_name"],
        "identity_version": company["identity_version"],
        "verification_status": company["verification_status"],
    }
    create_log_with_cursor(
        cur,
        action=f"company.{action}",
        user_id=actor_id,
        resource_type="company",
        resource_id=company["id"],
        details=event_payload,
    )
    enqueue_event(
        cur,
        event_type=f"company.{action}",
        aggregate_type="company",
        aggregate_id=company["id"],
        payload=event_payload,
    )


def _command_result(company: dict) -> dict:
    return {
        "company_id": company["id"],
        "legal_name": company["legal_name"],
        "verification_status": company["verification_status"],
        "identity_version": company["identity_version"],
    }


def _enum_value(value: object) -> str:
    return getattr(value, "value", value)


def get_company(company_id: str) -> dict | None:
    """Resolve a company ID to its canonical subject, preserving its redirect source."""
    requested_id = str(company_id)
    current_id = requested_id
    seen: set[str] = set()
    hops = 0

    while True:
        if current_id in seen:
            raise _merge_integrity_error()
        seen.add(current_id)

        company = company_repo.get_company_row(current_id)
        if company is None:
            if current_id == requested_id:
                return None
            raise _merge_integrity_error()

        merged_into_id = company.get("merged_into_id")
        if merged_into_id is None:
            canonical = dict(company)
            canonical["redirected_from"] = (
                requested_id if current_id != requested_id else None
            )
            return canonical

        if hops >= MAX_MERGE_HOPS:
            raise _merge_integrity_error()
        hops += 1
        current_id = str(merged_into_id)


def search_identity(query: str, limit: int = 10) -> dict:
    """Resolve a query deterministically to an exact company, candidates, or verification state."""
    rows = company_repo.search_identity_rows(query, limit)
    candidates_by_company_id: dict[str, dict] = {}
    for row in rows:
        canonical = get_company(row["id"])
        if canonical is None:
            continue
        candidate = _company_candidate(canonical, row)
        existing = candidates_by_company_id.get(candidate["company_id"])
        if existing is None or _candidate_sort_key(candidate) < _candidate_sort_key(existing):
            candidates_by_company_id[candidate["company_id"]] = candidate

    candidates = sorted(candidates_by_company_id.values(), key=_candidate_sort_key)
    if not candidates:
        return {"resolution": "pending_verification", "exact": None, "candidates": []}
    credit_candidates = [
        candidate for candidate in candidates if candidate["match_type"] == "credit_code"
    ]
    if len(credit_candidates) == 1:
        return {"resolution": "exact", "exact": credit_candidates[0], "candidates": []}
    if len(candidates) != 1:
        return {
            "resolution": "candidates",
            "exact": None,
            "candidates": candidates[:limit],
        }

    candidate = candidates[0]
    if candidate["verification_status"] != "verified":
        return {"resolution": "pending_verification", "exact": None, "candidates": []}
    if candidate["match_type"] == "legal_name":
        return {"resolution": "exact", "exact": candidate, "candidates": []}
    if (
        candidate["match_type"] == "alias"
        and candidate["confidence"] >= _ALIAS_EXACT_CONFIDENCE
    ):
        return {"resolution": "exact", "exact": candidate, "candidates": []}
    return {"resolution": "candidates", "exact": None, "candidates": candidates}


def _company_candidate(canonical: dict, matched_row: dict) -> dict:
    return {
        "company_id": canonical["id"],
        "legal_name": canonical["legal_name"],
        "unified_social_credit_code": canonical["unified_social_credit_code"],
        "registration_status": canonical["registration_status"],
        "verification_status": canonical["verification_status"],
        "match_type": matched_row["match_type"],
        "confidence": matched_row["confidence"],
        "redirected_from": canonical["redirected_from"],
    }


def _candidate_sort_key(candidate: dict) -> tuple[int, int, float, str, str]:
    return (
        {
            "credit_code": 0,
            "legal_name": 1,
            "alias": 2,
            "prefix": 3,
        }[candidate["match_type"]],
        0 if candidate["verification_status"] == "verified" else 1,
        -float(candidate["confidence"]),
        candidate["legal_name"],
        candidate["company_id"],
    )


def _merge_integrity_error() -> DomainError:
    return DomainError(
        "COMPANY_MERGE_INTEGRITY_ERROR",
        "企业合并重定向链数据完整性错误",
        409,
    )
