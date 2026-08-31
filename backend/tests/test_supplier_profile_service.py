"""Focused tests for supplier profile aggregation extensions."""

from datetime import datetime, timezone
from unittest.mock import Mock

from app.domains.supplier import service


def test_basic_info_uses_cached_contact_and_industry_without_writing_master(monkeypatch):
    """Cached enrichment is response-only and keeps per-field provenance."""
    baseinfo = {
        "items": {
            "result": {
                "industryAll": {"categoryCodeThird": "391", "categoryMiddle": "计算机制造业"},
                "website": "https://example.com",
                "phone": "400-000-0000",
                "email": "contact@example.com",
            }
        },
        "updated_at": datetime(2026, 8, 20, tzinfo=timezone.utc),
    }
    db = {
        "external_supplier_candidates": Mock(),
        "baseinfo": Mock(),
    }
    db["external_supplier_candidates"].find_one.return_value = None
    db["baseinfo"].find_one.return_value = baseinfo
    monkeypatch.setattr("app.db.mongo.get_db", lambda: db)

    master = {
        "name": "示例供应商",
        "source": "manual",
        "updated_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "categories": [],
    }

    enrichment = service._load_cached_enrichment(master)
    info = service._build_basic_info(master, enrichment)

    assert info["industry"] == "计算机通信和其他电子设备制造业"
    assert info["industry_source"] == "tianyancha_baseinfo_cache"
    assert info["website_url"] == "https://example.com"
    assert info["contact_phone"] == "400-000-0000"
    assert info["contact_email_source"] == "tianyancha_baseinfo_cache"
    assert master.get("industry") is None


def test_financial_snapshot_normalizes_history(monkeypatch):
    """Profile accepts both top-level and nested historical metric cache shapes."""
    db = {"financial_cache": Mock()}
    db["financial_cache"].find_one.return_value = {
        "metrics": {"revenue_growth": 12.5},
        "history": [
            {"year": 2024, "revenue": 100, "net_profit": 8},
            {"period": "2025", "metrics": {"revenue": 120, "net_profit": 10, "debt_ratio": 40}},
            {"period": "empty"},
        ],
    }
    monkeypatch.setattr("app.db.mongo.get_db", lambda: db)

    snapshot = service._build_financial_snapshot("示例供应商", {})

    assert snapshot["revenue_growth"] == 12.5
    assert snapshot["history"] == [
        {"period": "2024", "revenue": 100, "net_profit": 8, "debt_ratio": None, "cash_flow": None},
        {"period": "2025", "revenue": 120, "net_profit": 10, "debt_ratio": 40, "cash_flow": None},
    ]


def test_relationship_entities_include_local_supplier_link(monkeypatch):
    """Related entities expose a navigable id only when a local master exists."""
    monkeypatch.setattr(
        "app.domains.supplier.repo.get_supplier_by_name",
        lambda name: {"_id": "supplier-2"} if name == "关联供应商" else None,
    )

    entities = service._attach_supplier_links([
        {"name": "关联供应商", "relation_type": "供应链"},
        {"name": "外部企业", "relation_type": "分支"},
    ])

    assert entities[0]["supplier_id"] == "supplier-2"
    assert "supplier_id" not in entities[1]


def test_build_supplier_profile_passes_context_to_all_sections(monkeypatch):
    """Each profile section receives the current company and master snapshot."""
    master = {"_id": "supplier-view-1", "supplier_id": "supplier-1", "name": "示例供应商", "categories": []}
    monkeypatch.setattr("app.domains.supplier.repo.get_supplier", lambda supplier_id: master)
    changelog_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "app.domains.supplier.repo.get_changelog",
        lambda supplier_id, limit: changelog_calls.append((supplier_id, limit)) or [],
    )
    monkeypatch.setattr(service, "_load_cached_enrichment", lambda profile: {})
    monkeypatch.setattr(service, "_build_basic_info", lambda profile, enrichment: {"name": profile["name"]})

    calls: dict[str, tuple] = {}

    def record(section: str, *args):
        calls[section] = args
        return {} if section != "alerts" else []

    section_functions = {
        "risk": "_build_risk_summary",
        "sentiment": "_build_sentiment_summary",
        "compliance": "_build_compliance_status",
        "esg": "_build_esg_summary",
        "relationships": "_build_relationship_summary",
    }
    for section, function_name in section_functions.items():
        monkeypatch.setattr(service, function_name, lambda *args, _section=section: record(_section, *args))
    monkeypatch.setattr(service, "_build_alert_list", lambda *args: record("alerts", *args))
    monkeypatch.setattr(service, "_build_financial_snapshot", lambda *args: record("financial", *args))

    profile = service.build_supplier_profile("supplier-1")

    assert profile["basic_info"] == {"name": "示例供应商"}
    assert calls["risk"] == ("示例供应商",)
    assert calls["financial"] == ("示例供应商", master)
    assert calls["sentiment"] == ("示例供应商",)
    assert calls["compliance"] == ("示例供应商",)
    assert calls["esg"] == ("示例供应商",)
    assert calls["alerts"] == ("示例供应商",)
    assert calls["relationships"] == ("示例供应商",)
    assert changelog_calls == [("supplier-1", 20)]


def test_compliance_status_handles_empty_tianyancha_result(monkeypatch):
    """A no-result cache document must be treated as zero findings."""
    collections = {
        name: Mock() for name in (
            "lawSuit",
            "courtRegister",
            "executedPerson",
            "dishonesty",
            "abnormal",
            "punishmentInfo",
            "taxArrears",
        )
    }
    for collection in collections.values():
        collection.find_one.return_value = {"items": {"result": None}}
    monkeypatch.setattr("app.db.mongo.get_db", lambda: collections)
    monkeypatch.setattr(
        "app.domains.risk.sanctions_service.check_sanctions",
        lambda company_name: {"clean": True, "match_count": 0},
    )

    status = service._build_compliance_status("示例供应商")

    assert status["sanctions_clean"] is True
    assert status["lawsuit_count"] == 0
    assert status["administrative_penalty_count"] == 0


def test_compliance_status_includes_court_register_cases(monkeypatch):
    """Court registration records supplement lawsuit evidence in the profile."""
    collections = {name: Mock() for name in ("lawSuit", "courtRegister", "executedPerson", "dishonesty", "abnormal", "punishmentInfo", "taxArrears")}
    collections["lawSuit"].find_one.return_value = {"items": {"result": {"total": 2}}}
    collections["courtRegister"].find_one.return_value = {"items": {"result": {"total": 3}}}
    for name, collection in collections.items():
        if name not in {"lawSuit", "courtRegister"}:
            collection.find_one.return_value = None
    monkeypatch.setattr("app.db.mongo.get_db", lambda: collections)
    monkeypatch.setattr(
        "app.domains.risk.sanctions_service.check_sanctions",
        lambda company_name: {"clean": True, "match_count": 0},
    )

    status = service._build_compliance_status("示例供应商")

    assert status["lawsuit_count"] == 5
