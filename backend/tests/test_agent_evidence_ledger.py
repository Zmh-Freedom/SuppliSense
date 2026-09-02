"""P0 tests for Evidence Ledger, Claim validation and AgentAnswer."""

from datetime import datetime, timedelta, timezone

from app.graphs.agent_core.answer_contract import build_agent_answer
from app.graphs.agent_core.evidence_ledger import (
    Claim,
    EvidenceLedger,
    EvidenceStatus,
    build_evidence_record,
)


NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _record(
    evidence_id: str,
    *,
    entity_id: str = "supplier-1",
    dimension: str = "risk",
    status: EvidenceStatus = EvidenceStatus.AVAILABLE,
    data_mode: str = "formal",
    valid_until: datetime | None = None,
) -> object:
    return build_evidence_record(
        evidence_id=evidence_id,
        entity_id=entity_id,
        dimension=dimension,
        provider="official_registry",
        source_type="official",
        payload={"risk_score": 42},
        status=status,
        collected_at=NOW,
        valid_until=valid_until,
        data_mode=data_mode,
    )


def _claim(*refs: str, entity_id: str = "supplier-1", dimension: str = "risk", claim_id: str = "claim-1") -> Claim:
    return Claim(
        claim_id=claim_id,
        entity_id=entity_id,
        dimension=dimension,
        statement="风险评分为 42",
        value=42,
        evidence_refs=list(refs),
        confidence=0.9,
    )


def test_ledger_builds_stable_hash_and_supports_formal_evidence_claim() -> None:
    record = _record("ev-1")
    ledger = EvidenceLedger([record])

    result = ledger.validate_claim(_claim("ev-1"), now=NOW)

    assert record.content_hash
    assert result.claim.validation_status == "supported"
    assert result.valid_evidence_refs == ["ev-1"]


def test_claim_is_unsupported_when_evidence_missing_mismatched_or_stale() -> None:
    ledger = EvidenceLedger([
        _record("ev-mismatch", entity_id="supplier-2"),
        _record("ev-stale", valid_until=NOW - timedelta(seconds=1)),
        _record("ev-missing", status=EvidenceStatus.MISSING),
    ])

    mismatch = ledger.validate_claim(_claim("ev-mismatch"), now=NOW)
    stale = ledger.validate_claim(_claim("ev-stale"), now=NOW)
    missing = ledger.validate_claim(_claim("ev-missing"), now=NOW)

    assert mismatch.claim.validation_status == "unsupported"
    assert "entity_mismatch:ev-mismatch" in mismatch.claim.validation_reasons
    assert stale.claim.validation_status == "unsupported"
    assert missing.claim.validation_status == "unsupported"


def test_conflicting_and_synthetic_evidence_never_become_supported() -> None:
    conflict = EvidenceLedger([_record("ev-conflict", status=EvidenceStatus.CONFLICTING)])
    synthetic = EvidenceLedger([_record("ev-synthetic", status=EvidenceStatus.SYNTHETIC, data_mode="synthetic")])

    conflict_result = conflict.validate_claim(_claim("ev-conflict"), now=NOW)
    synthetic_result = synthetic.validate_claim(_claim("ev-synthetic"), now=NOW)

    assert conflict_result.claim.validation_status == "conflicting"
    assert synthetic_result.claim.validation_status == "partial"
    assert "synthetic_evidence:ev-synthetic" in synthetic_result.claim.validation_reasons


def test_coverage_excludes_missing_unavailable_and_synthetic_dimensions() -> None:
    ledger = EvidenceLedger([
        _record("risk", dimension="risk"),
        _record("esg", dimension="esg", status=EvidenceStatus.SYNTHETIC, data_mode="synthetic"),
        _record("sentiment", dimension="sentiment", status=EvidenceStatus.UNAVAILABLE),
    ])

    coverage = ledger.coverage(["risk", "esg", "sentiment", "compliance"])

    assert coverage.covered_dimensions == ["risk"]
    assert coverage.missing_dimensions == ["esg", "sentiment", "compliance"]
    assert coverage.coverage_ratio == 0.25


def test_agent_answer_excludes_unsupported_claims_and_marks_review() -> None:
    ledger = EvidenceLedger([_record("ev-supported"), _record("ev-missing", status=EvidenceStatus.MISSING)])
    answer = build_agent_answer(
        summary="已完成风险分析",
        ledger=ledger,
        claims=[_claim("ev-supported", claim_id="supported"), _claim("ev-missing", claim_id="missing")],
        required_dimensions=["risk", "sentiment"],
    )

    assert answer.status == "needs_review"
    assert [claim.claim_id for claim in answer.claims] == ["supported"]
    assert "缺少 sentiment 维度的正式证据" in answer.limitations
    assert answer.evidence_refs == ["ev-supported"]


def test_agent_answer_with_only_formal_supported_claims_is_completed() -> None:
    ledger = EvidenceLedger([_record("ev-supported")])
    answer = build_agent_answer(
        summary="风险评分有正式证据支持",
        ledger=ledger,
        claims=[_claim("ev-supported")],
        required_dimensions=["risk"],
    )

    assert answer.status == "completed"
    assert answer.claims[0].validation_status == "supported"
