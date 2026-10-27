from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.domains.risk import sanctions_service, sanctions_sources
from tests.evidence_validity_fixtures import ENTITY_ID, NOW, SCOPE, source_evidence


COMPANY_NAME = "虚构甲供应链有限公司"
ACTOR_ID = "user-fixture-a"
CONTRACT = {
    "source": "fixture-list-a",
    "list_name": "虚构测试名单",
    "authority": "虚构核验机构",
    "list_type": "fixture",
    "source_version": "fixture-v1",
    "scope": SCOPE,
    "required": True,
    "allowed_match_identifiers": ["unified_code"],
}
LEGACY_CONTRACT = {
    **CONTRACT,
    "legacy_projection": {
        "version": "fixture-legacy-v1",
        "statuses": {
            "no_match_in_scope": {"sanctions_score": 0, "sanctions_level": "low"},
            "confirmed_match": {"sanctions_score": 50, "sanctions_level": "critical"},
        },
    },
}


@pytest.fixture
def authorized_screening(monkeypatch):
    identity = {
        "company_id": ENTITY_ID,
        "company_name": COMPANY_NAME,
        "unified_code": "fixture-code-a",
    }
    monkeypatch.setattr(
        sanctions_service,
        "get_assessment_context",
        lambda: {"actor_id": ACTOR_ID, "actor_role": "analyst", "scope": SCOPE},
    )
    monkeypatch.setattr(sanctions_service, "resolve_identity", lambda *args, **kwargs: identity)
    monkeypatch.setattr(sanctions_service, "read_domestic_findings", lambda *args, **kwargs: [])
    return identity


def _source_result(contract=CONTRACT, *, status="empty", matches=None, **evidence_overrides):
    count = 0 if status == "empty" else 1
    evidence = source_evidence(
        source=contract["source"],
        source_version=contract["source_version"],
        scope=contract["scope"],
        status=status,
        query_receipt={"status": "success", "record_count": count},
        **evidence_overrides,
    )
    return {"evidence": evidence, "matches": matches or []}


def _configure_sources(monkeypatch, contracts, results):
    monkeypatch.setattr(sanctions_sources, "get_sanctions_source_contracts", lambda: contracts)
    monkeypatch.setattr(
        sanctions_sources,
        "load_sanctions_source",
        lambda contract, identity: deepcopy(results[contract["source"]]),
    )


def test_production_without_formal_sources_is_unavailable_and_has_no_sample_match(
    monkeypatch, authorized_screening
):
    monkeypatch.setattr(sanctions_sources, "get_sanctions_source_contracts", lambda: [])

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "unavailable"
    assert result["matches"] == []
    assert result["coverage"]["required_sources"] == []


def test_configured_source_without_query_receipt_is_not_queried(monkeypatch, authorized_screening):
    evidence = source_evidence(
        source=CONTRACT["source"],
        scope=CONTRACT["scope"],
        query_receipt={},
    )
    _configure_sources(monkeypatch, [CONTRACT], {CONTRACT["source"]: {"evidence": evidence, "matches": []}})

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "not_queried"
    assert result["coverage"]["sources"][0]["validity"]["status"] == "unknown"


def test_complete_current_zero_match_is_limited_to_declared_scope(monkeypatch, authorized_screening):
    _configure_sources(monkeypatch, [CONTRACT], {CONTRACT["source"]: _source_result()})

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "no_match_in_scope"
    assert result["coverage"]["status"] == "complete"
    assert result["matches"] == []
    assert SCOPE in result["coverage"]["required_sources"][0]["scope"]


def test_exact_name_or_alias_is_only_a_possible_match(monkeypatch, authorized_screening):
    record = {
        "record_id": "fixture-record-1",
        "name": "名单内虚构主体",
        "aliases": [COMPANY_NAME],
    }
    _configure_sources(
        monkeypatch,
        [CONTRACT],
        {CONTRACT["source"]: _source_result(status="available", matches=[record])},
    )

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "possible_match"
    assert result["matches"][0]["match_type"] == "possible"
    assert result["matches"][0]["validity"]["status"] == "available"


def test_stable_subject_identifier_match_is_confirmed(monkeypatch, authorized_screening):
    record = {
        "record_id": "fixture-record-2",
        "name": "名单内虚构主体",
        "identifiers": {"unified_code": "fixture-code-a"},
    }
    _configure_sources(
        monkeypatch,
        [CONTRACT],
        {CONTRACT["source"]: _source_result(status="available", matches=[record])},
    )

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "confirmed_match"
    assert result["matches"][0]["matched_on"] == "unified_code"
    assert result["matches"][0]["match_type"] == "confirmed"


def test_short_substring_does_not_count_as_a_match(monkeypatch, authorized_screening):
    record = {"record_id": "fixture-record-3", "name": "甲", "aliases": []}
    _configure_sources(
        monkeypatch,
        [CONTRACT],
        {CONTRACT["source"]: _source_result(status="available", matches=[record])},
    )

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "no_match_in_scope"
    assert result["matches"] == []


def test_company_suffix_is_not_removed_to_create_a_name_match(monkeypatch, authorized_screening):
    record = {"record_id": "fixture-record-3b", "name": "虚构甲供应链", "aliases": []}
    _configure_sources(
        monkeypatch,
        [CONTRACT],
        {CONTRACT["source"]: _source_result(status="available", matches=[record])},
    )

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "no_match_in_scope"
    assert result["matches"] == []


def test_sample_source_cannot_create_match(monkeypatch, authorized_screening):
    sample = _source_result(
        status="available",
        source_mode="sample",
        matches=[{"record_id": "sample-record", "name": COMPANY_NAME}],
    )
    _configure_sources(monkeypatch, [CONTRACT], {CONTRACT["source"]: sample})

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "unavailable"
    assert result["matches"] == []


def test_confirmed_match_survives_another_required_source_failure(monkeypatch, authorized_screening):
    failed_contract = {**CONTRACT, "source": "fixture-list-b", "source_version": "fixture-v2"}
    confirmed = _source_result(
        status="available",
        matches=[{"record_id": "fixture-record-4", "identifiers": {"unified_code": "fixture-code-a"}}],
    )
    failed = {
        "evidence": source_evidence(
            source=failed_contract["source"],
            source_version=failed_contract["source_version"],
            scope=failed_contract["scope"],
            status="failed",
            query_receipt={},
        ),
        "matches": [],
    }
    _configure_sources(
        monkeypatch,
        [CONTRACT, failed_contract],
        {CONTRACT["source"]: confirmed, failed_contract["source"]: failed},
    )

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "confirmed_match"
    assert result["coverage"]["status"] == "partial"
    assert result["matches"][0]["record_id"] == "fixture-record-4"


def test_no_match_with_missing_required_source_is_partial(monkeypatch, authorized_screening):
    missing_contract = {**CONTRACT, "source": "fixture-list-b", "source_version": "fixture-v2"}
    missing = {
        "evidence": source_evidence(
            source=missing_contract["source"],
            source_version=missing_contract["source_version"],
            scope=missing_contract["scope"],
            status="missing",
            query_receipt={},
        ),
        "matches": [],
    }
    _configure_sources(
        monkeypatch,
        [CONTRACT, missing_contract],
        {CONTRACT["source"]: _source_result(), missing_contract["source"]: missing},
    )

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "partial"
    assert result["coverage"]["status"] == "partial"


def test_only_expired_source_is_stale(monkeypatch, authorized_screening):
    expired = _source_result(expires_at="2026-10-22T00:00:00Z")
    _configure_sources(monkeypatch, [CONTRACT], {CONTRACT["source"]: expired})

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "stale"
    assert result["coverage"]["status"] == "insufficient"


def test_conflicting_source_is_reported_as_conflicting(monkeypatch, authorized_screening):
    conflicting = _source_result(conflict=True)
    _configure_sources(monkeypatch, [CONTRACT], {CONTRACT["source"]: conflicting})

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "conflicting"


def test_zero_receipt_with_returned_match_is_conflicting(monkeypatch, authorized_screening):
    contradictory = _source_result(
        status="empty",
        matches=[{"record_id": "fixture-record-contradiction", "name": COMPANY_NAME}],
    )
    _configure_sources(monkeypatch, [CONTRACT], {CONTRACT["source"]: contradictory})

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "conflicting"
    assert result["coverage"]["status"] == "insufficient"
    assert result["matches"] == []
    assert result["coverage"]["sources"][0]["validity"]["status"] == "conflicting"


def test_domestic_judicial_fact_is_not_a_sanctions_match(monkeypatch, authorized_screening):
    finding = {
        "kind": "executed_person",
        "count": 1,
        "validity": {"status": "available"},
        "source": "executedPerson",
    }
    monkeypatch.setattr(sanctions_service, "read_domestic_findings", lambda *args, **kwargs: [finding])
    monkeypatch.setattr(sanctions_sources, "get_sanctions_source_contracts", lambda: [])

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert result["screening_status"] == "unavailable"
    assert result["matches"] == []
    assert result["domestic_findings"] == [finding]


def test_legacy_projection_refuses_unknown_without_trusted_policy():
    with pytest.raises(ValueError):
        sanctions_service.project_legacy_sanctions(
            {"screening_status": "unavailable", "company_name": COMPANY_NAME}
        )


def test_legacy_projection_keeps_existing_field_names_and_types(monkeypatch, authorized_screening):
    _configure_sources(
        monkeypatch,
        [LEGACY_CONTRACT],
        {LEGACY_CONTRACT["source"]: _source_result(contract=LEGACY_CONTRACT)},
    )
    analysis = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    projected = sanctions_service.project_legacy_sanctions(analysis)

    assert set(projected) == {
        "company_name", "sanctions_score", "sanctions_level", "match_count", "matches", "clean"
    }
    assert type(projected["sanctions_score"]) is int
    assert type(projected["sanctions_level"]) is str
    assert type(projected["match_count"]) is int
    assert type(projected["clean"]) is bool
    assert projected["clean"] is True
    assert projected["sanctions_score"] == 0


def test_unscoped_call_does_not_load_sources_by_name(monkeypatch):
    loaded = []
    monkeypatch.setattr(sanctions_service, "get_assessment_context", lambda: {})
    monkeypatch.setattr(sanctions_sources, "get_sanctions_source_contracts", lambda: [CONTRACT])
    monkeypatch.setattr(
        sanctions_sources,
        "load_sanctions_source",
        lambda contract, identity: loaded.append((contract, identity)),
    )

    result = sanctions_service.analyze_sanctions(COMPANY_NAME, now=NOW)

    assert loaded == []
    assert result["screening_status"] == "unavailable"
