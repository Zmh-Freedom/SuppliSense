"""Evidence aggregation for the Agent Supervisor graph."""

from collections import defaultdict
from collections.abc import Mapping

from app.graphs.agent_supervisor.contracts import (
    AgentResult,
    EvidenceItem,
    EvidenceMergeResult,
)


_SOURCE_RANK = {
    "official": 0,
    "registry": 1,
    "internal": 2,
    "third_party": 3,
    "news": 4,
    "unknown": 5,
}
_FRESHNESS_RANK = {"fresh": 0, "unknown": 1, "stale": 2}


def _evidence_key(item: EvidenceItem) -> tuple[str | None, str | None, str]:
    return item.company_id, item.dimension, item.source


def _rank(item: EvidenceItem) -> tuple[int, int, float, str]:
    return (
        _SOURCE_RANK[item.source_type],
        _FRESHNESS_RANK[item.freshness],
        -item.confidence,
        item.evidence_id,
    )


def _conflict_key(item: EvidenceItem) -> tuple[str | None, str | None]:
    return item.company_id, item.dimension


def merge_evidence(results: Mapping[str, AgentResult]) -> EvidenceMergeResult:
    """Rank duplicate evidence while retaining all conflicting claims for review."""
    evidence_by_key: dict[tuple[str | None, str | None, str], list[EvidenceItem]] = defaultdict(list)
    missing_dimensions: list[str] = []

    for result in results.values():
        if result.status != "completed":
            missing_dimensions.append(result.agent)
            continue
        for item in result.evidence:
            evidence_by_key[_evidence_key(item)].append(item)

    merged_evidence: list[EvidenceItem] = []
    for items in evidence_by_key.values():
        claims = {item.claim for item in items if item.claim is not None}
        if len(claims) > 1:
            evidence_by_claim: dict[str | None, list[EvidenceItem]] = defaultdict(list)
            for item in items:
                evidence_by_claim[item.claim].append(item)
            merged_evidence.extend(min(claim_items, key=_rank) for claim_items in evidence_by_claim.values())
        else:
            merged_evidence.append(min(items, key=_rank))

    merged_evidence.sort(key=_rank)
    merged_conflict_groups: dict[tuple[str | None, str | None], list[EvidenceItem]] = defaultdict(list)
    for item in merged_evidence:
        merged_conflict_groups[_conflict_key(item)].append(item)
    conflicts = [
        sorted(items, key=_rank)
        for items in merged_conflict_groups.values()
        if len({item.claim for item in items if item.claim is not None}) > 1
    ]
    unique_missing = sorted(set(missing_dimensions))
    overall_confidence = (
        sum(item.confidence for item in merged_evidence) / len(merged_evidence)
        if merged_evidence
        else 0.0
    )
    return EvidenceMergeResult(
        evidence=merged_evidence,
        conflicts=conflicts,
        missing_dimensions=unique_missing,
        overall_confidence=overall_confidence,
        requires_review=bool(conflicts or unique_missing),
    )
