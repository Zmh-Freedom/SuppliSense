from copy import deepcopy
from datetime import datetime, timezone

from app.domains.risk import sanctions_service
from app.graphs.agent_core.evidence_ledger import Claim, EvidenceLedger, EvidenceRecord
from app.tools.contracts import SanctionsOutput
from app.tools.evidence import attach_screening_evidence
from tests.evidence_validity_fixtures import ENTITY_ID, NOW, SCOPE


def _analysis(*, status="unavailable", sources=None, matches=None, coverage="insufficient"):
    return {
        "schema_version": 1,
        "company_name": "虚构甲供应链有限公司",
        "entity_id": ENTITY_ID,
        "scope": SCOPE,
        "screening_status": status,
        "coverage": {"status": coverage, "sources": sources or [], "required_sources": []},
        "matches": matches or [],
        "domestic_findings": [],
        "limitations": ["仅表示测试范围内的来源状态。"],
        "checked_at": NOW.isoformat(),
        "legacy_projection_policy": None,
    }


def _source(*, source="fixture-list-a", status="available", match_count=1):
    evidence = {
        "source": source,
        "source_version": "fixture-list-v1",
        "source_mode": "formal",
        "status": status,
        "observed_at": "2026-10-22T00:00:00+00:00",
        "collected_at": "2026-10-22T00:05:00+00:00",
        "expires_at": "2026-10-24T00:00:00+00:00",
        "query_receipt": {"status": "success", "record_count": match_count},
        "coverage_partial": False,
        "conflict": False,
    }
    validity = {
        "status": status,
        "observed_at": evidence["observed_at"],
        "collected_at": evidence["collected_at"],
        "expires_at": evidence["expires_at"],
    }
    return {"source": source, "scope": SCOPE, "required": True, "evidence": evidence, "validity": validity}


def test_unavailable_screening_has_no_clean_or_zero_match_claim():
    result = attach_screening_evidence(_analysis(), entity_id=ENTITY_ID)
    validated = SanctionsOutput.model_validate(result)

    assert validated.clean is None
    assert validated.match_count == 0
    assert not any(claim.get("fact_path") in {"clean", "match_count"} for claim in result["claims"])
    assert result["status"] == "unavailable"


def test_domestic_facts_remain_claimable_when_sanctions_lists_are_unavailable():
    analysis = _analysis()
    analysis["domestic_findings"] = [{
        "kind": "lawsuit",
        "count": 2,
        "source": "lawSuit",
        "validity": {
            "status": "available",
            "source_version": "fixture-court-v1",
            "observed_at": "2026-10-22T00:00:00+00:00",
            "collected_at": "2026-10-22T00:05:00+00:00",
            "expires_at": "2026-10-24T00:00:00+00:00",
            "query_receipt": {"status": "success", "record_count": 2},
        },
    }]

    result = attach_screening_evidence(analysis, entity_id=ENTITY_ID)

    claim = next(item for item in result["claims"] if item.get("fact_path") == "count")
    evidence = next(item for item in result["evidence_records"] if item["facts"].get("domestic_finding_kind"))
    assert result["clean"] is None
    assert claim["value"] == 2
    assert claim["evidence_refs"] == [evidence["evidence_id"]]
    assert evidence["facts"]["observed_at"] == "2026-10-22T00:00:00+00:00"
    assert datetime.fromisoformat(evidence["valid_until"].replace("Z", "+00:00")) == datetime(
        2026, 10, 24, 0, 0, tzinfo=timezone.utc
    )


def test_source_evidence_preserves_observation_collection_and_expiry_times():
    source = _source()
    match = {
        "source": source["source"],
        "record_id": "fixture-record-a",
        "record_name": "名单内虚构主体",
        "match_type": "confirmed",
        "matched_on": "unified_code",
        "validity": {"status": "available"},
    }
    analysis = _analysis(status="confirmed_match", sources=[source], matches=[match], coverage="complete")

    result = attach_screening_evidence(analysis, entity_id=ENTITY_ID)

    record = next(item for item in result["evidence_records"] if item.get("facts", {}).get("record_id"))
    parsed = EvidenceRecord.model_validate(record)
    assert parsed.facts["observed_at"] == source["evidence"]["observed_at"]
    assert parsed.facts["collected_at"] == source["evidence"]["collected_at"]
    assert parsed.valid_until == datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)
    assert result["claims"][0]["evidence_refs"] == [parsed.evidence_id]


def test_partial_screening_keeps_current_hit_claim_and_source_limitations():
    current = _source()
    failed = _source(source="fixture-list-b", status="failed", match_count=0)
    failed["evidence"].update({"observed_at": None, "collected_at": None, "expires_at": None, "query_receipt": {"status": "failed"}})
    match = {
        "source": current["source"],
        "record_id": "fixture-record-b",
        "record_name": "名单内虚构主体",
        "match_type": "confirmed",
        "matched_on": "unified_code",
        "validity": {"status": "available"},
    }
    analysis = _analysis(
        status="confirmed_match",
        sources=[current, failed],
        matches=[match],
        coverage="partial",
    )

    result = attach_screening_evidence(analysis, entity_id=ENTITY_ID)
    ledger = EvidenceLedger([EvidenceRecord.model_validate(item) for item in result["evidence_records"]])

    assert result["status"] == "partial"
    assert len(result["claims"]) == 1
    assert result["claims"][0]["fact_path"] == "record_name"
    assert any(
        item["facts"].get("limitations")
        for item in result["evidence_records"]
    )
    assert ledger.validate_claim(Claim.model_validate(result["claims"][0]), now=NOW).claim.validation_status == "supported"


def test_check_sanctions_tool_uses_read_model_and_source_evidence(monkeypatch):
    analysis = _analysis()
    monkeypatch.setattr(sanctions_service, "analyze_sanctions", lambda _name: deepcopy(analysis))

    from app.domains.risk.tools_analysis import check_sanctions

    result = check_sanctions.invoke({"company_name": analysis["company_name"]})

    assert result["clean"] is None
    assert result["status"] == "unavailable"
    assert result["claims"] == []
