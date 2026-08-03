"""Framework-independent Company Identity read and resolution services."""

from app.core.errors import DomainError
from app.domains.company import repo as company_repo


MAX_MERGE_HOPS = 20
_ALIAS_EXACT_CONFIDENCE = 0.95


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
        candidates_by_company_id.setdefault(candidate["company_id"], candidate)

    candidates = list(candidates_by_company_id.values())
    if not candidates:
        return {"resolution": "pending_verification", "exact": None, "candidates": []}
    if len(candidates) != 1:
        return {"resolution": "candidates", "exact": None, "candidates": candidates}

    candidate = candidates[0]
    if candidate["match_type"] == "credit_code":
        return {"resolution": "exact", "exact": candidate, "candidates": []}
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


def _merge_integrity_error() -> DomainError:
    return DomainError(
        "COMPANY_MERGE_INTEGRITY_ERROR",
        "企业合并重定向链数据完整性错误",
        409,
    )
