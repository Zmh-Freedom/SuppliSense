"""Read-only identity adaptation for sourcing-risk candidates."""

from app.domains.company.service import search_identity


def resolve_candidate_identity(candidate: dict) -> dict:
    """Bind a canonical company only when P1 resolves the candidate exactly."""
    query = candidate.get("unified_social_credit_code") or candidate["supplier_name"]
    resolution = search_identity(query)

    if resolution.get("resolution") == "exact":
        exact = resolution.get("exact") or {}
        return {
            "identity_status": "exact",
            "company_id": exact.get("company_id"),
            "identity_candidates": [],
        }

    identity_status = resolution.get("resolution")
    if identity_status not in {"candidates", "pending_verification"}:
        identity_status = "pending_verification"
    return {
        "identity_status": identity_status,
        "company_id": None,
        "identity_candidates": resolution.get("candidates", []),
        "identity_review": True,
        "score_eligible": False,
        "identity_source_snapshot": resolution,
    }
