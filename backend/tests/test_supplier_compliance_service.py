from copy import deepcopy

import pytest

from app.domains.supplier import compliance_service
from tests.evidence_validity_fixtures import ENTITY_ID, NOW, SCOPE


COMPANY_NAME = "虚构甲供应链有限公司"


class FakeCollection:
    def __init__(self, document):
        self.document = document
        self.queries = []

    def find_one(self, query):
        self.queries.append(query)
        return deepcopy(self.document)


class FakeDatabase(dict):
    def __getitem__(self, key):
        return super().get(key, FakeCollection(None))


def _source_document(name, count):
    return {
        "name": name,
        "entity_id": ENTITY_ID,
        "scope": SCOPE,
        "source_mode": "formal",
        "source_version": "fixture-provider-v1",
        "observed_at": "2026-10-22T00:00:00Z",
        "collected_at": "2026-10-22T00:05:00Z",
        "expires_at": "2026-10-24T00:00:00Z",
        "_query_receipt": {"status": "success"},
        "items": {"result": {"total": count}},
    }


def test_domestic_findings_keep_valid_judicial_evidence_separate_from_screening(monkeypatch):
    collection = FakeCollection(_source_document(COMPANY_NAME, 2))
    monkeypatch.setattr(
        compliance_service,
        "get_db",
        lambda: FakeDatabase({"executedPerson": collection}),
    )

    findings = compliance_service.read_domestic_findings(
        COMPANY_NAME, entity_id=ENTITY_ID, scope=SCOPE, now=NOW
    )

    executed = next(item for item in findings if item["source"] == "executedPerson")
    assert executed["kind"] == "executed_person"
    assert executed["count"] == 2
    assert executed["validity"]["status"] == "available"


def test_missing_query_receipt_never_becomes_verified_zero(monkeypatch):
    document = {"name": COMPANY_NAME, "items": {"result": {}}}
    collection = FakeCollection(document)
    monkeypatch.setattr(
        compliance_service,
        "get_db",
        lambda: FakeDatabase({"dishonesty": collection}),
    )

    findings = compliance_service.read_domestic_findings(
        COMPANY_NAME, entity_id=ENTITY_ID, scope=SCOPE, now=NOW
    )

    dishonesty = next(item for item in findings if item["source"] == "dishonesty")
    assert dishonesty["count"] is None
    assert dishonesty["validity"]["status"] == "unknown"
    assert dishonesty["validity"]["query_receipt"] == {"status": "unavailable", "reason": "missing_query_receipt"}


def test_count_with_receipt_but_without_validity_policy_is_not_current(monkeypatch):
    document = {
        "name": COMPANY_NAME,
        "entity_id": ENTITY_ID,
        "scope": SCOPE,
        "items": {"result": {"total": 0}},
    }
    collection = FakeCollection(document)
    monkeypatch.setattr(
        compliance_service,
        "get_db",
        lambda: FakeDatabase({"taxArrears": collection}),
    )

    findings = compliance_service.read_domestic_findings(
        COMPANY_NAME, entity_id=ENTITY_ID, scope=SCOPE, now=NOW
    )

    tax_arrears = next(item for item in findings if item["source"] == "taxArrears")
    assert tax_arrears["count"] is None
    assert tax_arrears["validity"]["status"] == "unknown"


def test_missing_domestic_collection_is_not_displayed_as_verified_zero(monkeypatch):
    monkeypatch.setattr(compliance_service, "get_db", lambda: FakeDatabase())

    findings = compliance_service.read_domestic_findings(
        COMPANY_NAME, entity_id=ENTITY_ID, scope=SCOPE, now=NOW
    )

    assert len(findings) == 7
    assert all(item["count"] is None for item in findings)
    assert all(item["validity"]["status"] == "missing" for item in findings)


def test_mongo_connection_failure_marks_domestic_sources_failed_without_raising(monkeypatch):
    def fail_to_connect():
        raise RuntimeError("fixture database unavailable")

    monkeypatch.setattr(compliance_service, "get_db", fail_to_connect)

    findings = compliance_service.read_domestic_findings(
        COMPANY_NAME, entity_id=ENTITY_ID, scope=SCOPE, now=NOW
    )

    assert len(findings) == 7
    assert all(item["count"] is None for item in findings)
    assert all(item["validity"]["status"] == "failed" for item in findings)


def test_supplier_status_preserves_domestic_facts_when_screening_is_unavailable(monkeypatch):
    domestic = [{
        "kind": "lawsuit",
        "count": 3,
        "validity": {"status": "available"},
        "source": "lawSuit",
    }]
    monkeypatch.setattr(
        "app.domains.supplier.repo.get_supplier",
        lambda supplier_id: {"_id": supplier_id, "supplier_id": supplier_id, "name": COMPANY_NAME},
    )
    monkeypatch.setattr(
        "app.domains.risk.sanctions_service.analyze_sanctions",
        lambda name, now=None: {
            "schema_version": 1,
            "company_name": name,
            "entity_id": ENTITY_ID,
            "scope": SCOPE,
            "screening_status": "unavailable",
            "coverage": {"status": "insufficient", "sources": [], "required_sources": []},
            "matches": [],
            "domestic_findings": domestic,
            "limitations": ["未配置正式名单来源"],
            "checked_at": NOW.isoformat(),
        },
    )

    status = compliance_service.build_supplier_compliance_status("supplier-fixture-a", now=NOW)

    assert status["schema_version"] == 1
    assert status["screening"]["screening_status"] == "unavailable"
    assert status["domestic_findings"] == domestic
    assert "sanctions_clean" not in status


def test_supplier_status_reuses_domestic_results_from_single_screening_read(monkeypatch):
    calls = []
    domestic = [{
        "kind": "dishonesty",
        "count": 1,
        "validity": {"status": "available"},
        "source": "dishonesty",
    }]
    monkeypatch.setattr(
        "app.domains.supplier.repo.get_supplier",
        lambda supplier_id: {"_id": supplier_id, "supplier_id": supplier_id, "name": COMPANY_NAME},
    )

    def analyze(name, now=None):
        calls.append((name, now))
        return {
            "schema_version": 1,
            "company_name": name,
            "entity_id": ENTITY_ID,
            "scope": SCOPE,
            "screening_status": "unavailable",
            "coverage": {"status": "insufficient", "sources": [], "required_sources": []},
            "matches": [],
            "domestic_findings": domestic,
            "limitations": [],
            "checked_at": NOW.isoformat(),
        }

    monkeypatch.setattr("app.domains.risk.sanctions_service.analyze_sanctions", analyze)

    status = compliance_service.build_supplier_compliance_status("supplier-fixture-a", now=NOW)

    assert calls == [(COMPANY_NAME, NOW)]
    assert status["domestic_findings"] is domestic
