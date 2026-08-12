"""Behavior tests for sourcing-risk candidate identity adaptation."""

from app.domains.sourcing_risk import identity_service


def test_exact_identity_binds_only_the_canonical_company_id(monkeypatch):
    """Binding an ID from a non-exact P1 result would bypass identity review."""
    monkeypatch.setattr(
        identity_service,
        "search_identity",
        lambda *_: {"resolution": "exact", "exact": {"company_id": "company-id"}},
    )

    result = identity_service.resolve_candidate_identity({"supplier_name": "示例科技"})

    assert result == {
        "identity_status": "exact",
        "company_id": "company-id",
        "identity_candidates": [],
        "score_eligible": True,
    }


def test_exact_result_without_company_id_requires_identity_review(monkeypatch):
    """Accepting an empty exact ID would allow a name to become a durable identity."""
    p1_result = {"resolution": "exact", "exact": {"company_id": None}, "candidates": []}
    monkeypatch.setattr(identity_service, "search_identity", lambda *_: p1_result)

    result = identity_service.resolve_candidate_identity({"supplier_name": "示例科技"})

    assert result == {
        "identity_status": "pending_verification",
        "company_id": None,
        "identity_candidates": [],
        "identity_review": True,
        "score_eligible": False,
        "identity_source_snapshot": p1_result,
    }


def test_ambiguous_identity_never_receives_a_company_id(monkeypatch):
    """Promoting an ambiguous match would make an unreviewed company scoreable."""
    p1_result = {
        "resolution": "candidates",
        "exact": None,
        "candidates": [{"company_id": "a"}, {"company_id": "b"}],
    }
    monkeypatch.setattr(identity_service, "search_identity", lambda *_: p1_result)

    result = identity_service.resolve_candidate_identity({"supplier_name": "示例科技"})

    assert result["identity_status"] == "candidates"
    assert result["company_id"] is None
    assert result["identity_candidates"] == [{"company_id": "a"}, {"company_id": "b"}]
    assert result["identity_review"] is True
    assert result["score_eligible"] is False
    assert result["identity_source_snapshot"] == p1_result


def test_pending_identity_uses_credit_code_before_supplier_name_and_requires_review(monkeypatch):
    """Falling back to a name or scoring pending identities would weaken the ID boundary."""
    queries: list[str] = []
    p1_result = {"resolution": "pending_verification", "exact": None, "candidates": []}

    def fake_search_identity(query: str) -> dict:
        queries.append(query)
        return p1_result

    monkeypatch.setattr(identity_service, "search_identity", fake_search_identity)

    result = identity_service.resolve_candidate_identity(
        {"supplier_name": "示例科技", "unified_social_credit_code": "91310000EXAMPLE"}
    )

    assert queries == ["91310000EXAMPLE"]
    assert result == {
        "identity_status": "pending_verification",
        "company_id": None,
        "identity_candidates": [],
        "identity_review": True,
        "score_eligible": False,
        "identity_source_snapshot": p1_result,
    }
