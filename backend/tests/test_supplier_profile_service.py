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
