"""Contract tests for bounded, local-first supplier discovery."""

from unittest.mock import Mock

from app.domains.sourcing_risk import discovery_service


def _requirement(**overrides: object) -> dict:
    return {
        "category": "钢材",
        "specification": "热轧板",
        "region": "华东",
        **overrides,
    }


def _policy(**overrides: object) -> dict:
    return {"minimum_candidate_count": 2, **overrides}


def _local_candidate(name: str) -> dict:
    return {
        "supplier_id": f"local-{name}",
        "supplier_name": name,
        "categories": ["钢材"],
        "specifications": ["热轧板"],
        "regions": ["华东"],
    }


def _external_candidate(name: str, source: str) -> dict:
    return {
        "supplier_name": name,
        "categories": ["钢材"],
        "specifications": ["热轧板"],
        "regions": ["华东"],
        "source": source,
        "verification_status": "unverified",
    }


def test_discovery_loop_stops_before_all_external_stages_when_local_supply_is_sufficient(monkeypatch):
    local = [_local_candidate("本地一"), _local_candidate("本地二")]
    monkeypatch.setattr(discovery_service, "discover_local_candidates", lambda *_: local)
    tianyancha = Mock()
    web = Mock()
    enrichment = Mock()
    monkeypatch.setattr(discovery_service, "_search_tianyancha_candidates", tianyancha)
    monkeypatch.setattr(discovery_service, "_search_web_candidates", web)
    monkeypatch.setattr(discovery_service, "_enrich_external_contacts", enrichment)

    result = discovery_service.discover_candidates(_requirement(), _policy())

    assert result["external_status"] == "not_required"
    assert result["external_stop_reason"] == "local_supply_sufficient"
    assert result["external_loop"]["iterations"] == 0
    tianyancha.assert_not_called()
    web.assert_not_called()
    enrichment.assert_not_called()


def test_discovery_loop_uses_tianyancha_before_web_and_enriches_staged_candidates(monkeypatch):
    local = [_local_candidate("本地一")]
    events: list[str] = []
    monkeypatch.setattr(discovery_service, "discover_local_candidates", lambda *_: local)
    monkeypatch.setattr(
        discovery_service,
        "_search_tianyancha_candidates",
        lambda *_: events.append("tianyancha") or [_external_candidate("天眼查企业", "tianyancha_search")],
    )
    monkeypatch.setattr(
        discovery_service,
        "_search_web_candidates",
        lambda *_: events.append("web") or [_external_candidate("联网企业", "web_search")],
    )
    monkeypatch.setattr(
        discovery_service,
        "_enrich_external_contacts",
        lambda candidates: events.append("contact") or [
            {**candidate, "contact_enrichment_status": "partial"}
            for candidate in candidates
        ],
    )

    result = discovery_service.discover_candidates(_requirement(), _policy())

    assert events == ["tianyancha", "contact"]
    assert result["external_status"] == "staged"
    assert result["external_stop_reason"] == "candidate_supply_sufficient"
    assert result["external_loop"]["iterations"] == 2
    assert result["external_candidates"][0]["status"] == "staged_candidate"
    assert result["external_candidates"][0]["verification_status"] == "unverified"
    assert result["external_candidates"][0]["supplier_id"] is None
    assert result["external_candidates"][0]["company_id"] is None


def test_discovery_loop_degrades_after_provider_failures_and_keeps_local_candidates(monkeypatch):
    local = [_local_candidate("本地一")]
    monkeypatch.setattr(discovery_service, "discover_local_candidates", lambda *_: local)
    monkeypatch.setattr(
        discovery_service,
        "_search_tianyancha_candidates",
        lambda *_: (_ for _ in ()).throw(RuntimeError("tyc unavailable")),
    )
    monkeypatch.setattr(
        discovery_service,
        "_search_web_candidates",
        lambda *_: (_ for _ in ()).throw(RuntimeError("web unavailable")),
    )
    monkeypatch.setattr(discovery_service, "_enrich_external_contacts", Mock())

    result = discovery_service.discover_candidates(_requirement(), _policy())

    assert result["source"] == "local"
    assert result["local_candidates"] == local
    assert result["external_candidates"] == []
    assert result["external_status"] == "failed"
    assert result["external_stop_reason"] == "external_sources_failed"
    assert result["external_loop"]["iterations"] == 2
    assert result["external_loop"]["failed_stages"] == ["tianyancha", "web_search"]
