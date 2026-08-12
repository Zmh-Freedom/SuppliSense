"""Behavior tests for local-first sourcing-risk supplier discovery."""

from unittest.mock import Mock

from app.domains.sourcing_risk import discovery_service
from app.domains.sourcing import supplier_repo


def _requirement(**overrides: object) -> dict:
    return {
        "category": "摄像头",
        "specification": "IP67",
        "region": "华东",
        "qualifications": "ISO9001",
        **overrides,
    }


def _policy(**overrides: object) -> dict:
    return {"minimum_candidate_count": 3, **overrides}


def _candidate(name: str, **overrides: object) -> dict:
    return {
        "supplier_id": f"supplier-{name}",
        "supplier_name": name,
        "categories": ["摄像头", "IP67"],
        "specifications": ["IP67"],
        "regions": ["华东"],
        "status": "active",
        "qualifications": ["ISO9001"],
        "capacity": {"available": 100},
        "updated_at": "2026-08-12T00:00:00+00:00",
        "match_reasons": ["category:摄像头"],
        **overrides,
    }


def test_local_candidates_prevent_external_provider_call(monkeypatch):
    """Removing local sufficiency short-circuit would make unnecessary external calls."""
    local = [_candidate("a"), _candidate("b"), _candidate("c")]
    monkeypatch.setattr(discovery_service, "search_local_suppliers", lambda *_: local)
    external = Mock()
    monkeypatch.setattr(discovery_service, "search_external_provider", external)

    result = discovery_service.discover_candidates(_requirement(), _policy())

    assert result["source"] == "local"
    assert result["local_candidates"] == local
    assert result["external_status"] == "not_required"
    external.assert_not_called()


def test_insufficient_local_candidates_retain_local_results_and_stage_external(monkeypatch):
    """Replacing local evidence on fallback would hide the local-first search result."""
    local = [_candidate("local")]
    monkeypatch.setattr(discovery_service, "search_local_suppliers", lambda *_: local)
    monkeypatch.setattr(
        discovery_service,
        "search_external_provider",
        lambda _: [{"name": "外部公司", "source_reference": "tyc:1"}],
    )

    result = discovery_service.discover_candidates(_requirement(), _policy())

    assert result["source"] == "local_and_external"
    assert result["local_candidates"] == local
    assert result["external_status"] == "staged"
    assert result["external_candidates"] == [
        {
            "name": "外部公司",
            "source_reference": "tyc:1",
            "status": "staged_candidate",
            "supplier_id": None,
            "company_id": None,
        }
    ]


def test_external_candidates_are_staged_without_supplier_or_company_id():
    """External discovery must not create or resolve durable supplier identities."""
    staged = discovery_service.stage_external_candidates(
        "run-id", [{"name": "外部公司", "source_reference": "tyc:1"}]
    )

    assert staged[0]["status"] == "staged_candidate"
    assert staged[0]["supplier_id"] is None
    assert staged[0]["company_id"] is None
    assert staged[0]["run_id"] == "run-id"


def test_sufficiency_requires_every_explicit_constraint_to_be_covered():
    """Counting candidates alone would allow a required qualification to be missed."""
    candidates = [
        _candidate("a"),
        _candidate("b"),
        _candidate("c", qualifications=[]),
    ]

    assert discovery_service.is_candidate_supply_sufficient(
        candidates, _requirement(qualifications="ISO9001, ISO14001"), _policy()
    ) is False


def test_category_coverage_without_specification_coverage_uses_external_provider(monkeypatch):
    """Checking specifications against categories would skip needed external discovery."""
    local = [
        _candidate(
            name,
            categories=["摄像头", "IP67"],
            specifications=["IP65"],
        )
        for name in ("a", "b", "c")
    ]
    monkeypatch.setattr(discovery_service, "search_local_suppliers", lambda *_: local)
    external = Mock(return_value=[])
    monkeypatch.setattr(discovery_service, "search_external_provider", external)

    result = discovery_service.discover_candidates(_requirement(), _policy())

    assert result["source"] == "local_and_external"
    assert result["external_status"] == "staged"
    external.assert_called_once_with(_requirement())


def test_local_repository_search_filters_active_category_specification_region_and_qualification(
    monkeypatch,
):
    """Relaxing any local filter would pass candidates that violate the request."""
    documents = [
        {
            "_id": "match", "name": "符合公司", "status": "active",
            "categories": ["摄像头"], "specifications": ["IP67"],
            "regions": ["华东"], "qualifications": ["ISO9001"],
            "capacity": {"available": 10}, "updated_at": "today",
        },
        {
            "_id": "inactive", "name": "停用公司", "status": "inactive",
            "categories": ["摄像头"], "specifications": ["IP67"],
            "regions": ["华东"], "qualifications": ["ISO9001"],
        },
        {
            "_id": "wrong-spec", "name": "规格错误公司", "status": "active",
            "categories": ["摄像头"], "specifications": ["IP65"],
            "regions": ["华东"], "qualifications": ["ISO9001"],
        },
    ]

    class Suppliers:
        def find(self, query):
            assert query == {"status": "active"}
            return [document for document in documents if document["status"] == "active"]

    monkeypatch.setattr(supplier_repo, "get_db", lambda: {"suppliers": Suppliers()})
    monkeypatch.setattr(
        supplier_repo,
        "resolve_supplier_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must remain read-only")),
    )

    candidates = supplier_repo.search_for_sourcing_v2(_requirement())

    assert [candidate["supplier_id"] for candidate in candidates] == ["match"]
    assert candidates[0]["match_reasons"] == [
        "category:摄像头", "specification:IP67", "region:华东", "qualifications:ISO9001"
    ]
