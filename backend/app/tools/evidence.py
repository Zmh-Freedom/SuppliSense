"""Normalize domain-tool observations into the common evidence envelope."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def attach_tool_evidence(
    payload: dict[str, Any],
    *,
    tool_name: str,
    entity_id: str,
    dimension: str,
    source_type: str = "domain_service_result",
    claim_fields: list[str] | None = None,
    claim_subject: str | None = None,
) -> dict[str, Any]:
    """Attach auditable evidence without changing the domain service contract.

    Existing provider evidence is preserved.  The fallback record describes the
    returned domain-service observation itself, so it is not confused with an
    independent external source; Task 13 will bind individual claims to its
    facts.  Error-only payloads are explicit non-success outcomes and receive no
    evidence record.
    """
    result = dict(payload)
    entity_id = _active_entity_id(entity_id)
    assessment_status = result.get("assessment_status")
    if assessment_status == "missing_supplier":
        result.setdefault("status", "not_found")
        return result
    if assessment_status in {"missing_data", "insufficient_data"}:
        result.setdefault("status", "unavailable")
        return result
    if result.get("status") in {"not_found", "unavailable", "failed", "invalid", "denied"}:
        return result
    if result.get("error") and not any(
        result.get(key) for key in ("risk_score", "total_score", "companies", "results", "candidates", "items")
    ):
        result.setdefault("status", "not_found")
        return result
    if result.get("evidence_records") or result.get("evidence_refs"):
        return result

    data_mode = "synthetic" if result.get("data_mode") == "synthetic" else "formal"
    if result.get("assessment_data_mode") == "demo":
        data_mode = "synthetic"
    now = datetime.now(timezone.utc).isoformat()
    legacy_records = result.get("evidence")
    if isinstance(legacy_records, list) and legacy_records:
        result["evidence_records"] = _normalize_legacy_records(
            legacy_records,
            tool_name=tool_name,
            entity_id=entity_id,
            dimension=dimension,
            source_type=source_type,
            fallback_collected_at=now,
        )
        _append_claims(result, claim_fields, result["evidence_records"], claim_subject)
        return result
    from app.graphs.agent_core.evidence_ledger import build_evidence_record

    result["evidence_records"] = [build_evidence_record(
        evidence_id=f"{tool_name}:{entity_id}:{dimension}",
        entity_id=entity_id,
        dimension=dimension,
        provider=tool_name,
        source_type=source_type,
        status=_status_value(result.get("status")),
        collected_at=datetime.fromisoformat(now),
        data_mode=data_mode,
        payload={
            key: value
            for key, value in result.items()
            if key not in {"evidence_records", "claims", "evidence", "evidence_refs"}
        },
    ).model_dump(mode="json")]
    _append_claims(result, claim_fields, result["evidence_records"], claim_subject)
    return result


def _active_entity_id(fallback: str) -> str:
    """Use the Harness task entity while preserving direct tool compatibility.

    Domain tool wrappers historically derive an entity from the company name,
    while Harness tasks use the canonical supplier/company ID from session
    memory.  The executor context is the only shared boundary where both are
    available, so evidence must prefer it to keep ledger coverage stable.
    """
    from app.tools.executor import get_active_tool_context

    context = get_active_tool_context()
    context_entity_id = context.entity_id if context else None
    return str(context_entity_id or fallback)


def _normalize_legacy_records(
    records: list[Any],
    *,
    tool_name: str,
    entity_id: str,
    dimension: str,
    source_type: str,
    fallback_collected_at: str,
) -> list[dict[str, Any]]:
    """Convert historical provider records into the complete evidence contract."""
    from app.graphs.agent_core.evidence_ledger import build_evidence_record

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            continue
        status = _status_value(item.get("status"))
        data_mode = "synthetic" if item.get("data_mode") == "synthetic" else "formal"
        collected_at = _parse_datetime(
            item.get("collected_at") or item.get("observed_at") or fallback_collected_at
        )
        facts = item.get("facts")
        payload = dict(facts) if isinstance(facts, dict) else {
            key: value for key, value in item.items()
            if key not in {
                "evidence_id", "entity_id", "dimension", "provider", "source",
                "source_type", "status", "collected_at", "observed_at", "data_mode",
                "endpoint", "query", "raw_payload_ref",
            }
        }
        normalized.append(build_evidence_record(
            evidence_id=str(item.get("evidence_id") or f"{tool_name}:{entity_id}:{dimension}:{index}"),
            entity_id=str(item.get("entity_id") or entity_id),
            dimension=str(item.get("dimension") or dimension),
            provider=str(item.get("provider") or item.get("source") or tool_name),
            source_type=str(item.get("source_type") or item.get("source") or source_type),
            status=status,
            collected_at=collected_at,
            data_mode=data_mode,
            endpoint=str(item["endpoint"]) if item.get("endpoint") else None,
            query=item.get("query") if isinstance(item.get("query"), dict) else None,
            raw_payload_ref=str(item["raw_payload_ref"]) if item.get("raw_payload_ref") else None,
            payload=payload,
        ).model_dump(mode="json"))
    return normalized


def _status_value(value: Any) -> Any:
    from app.graphs.agent_core.evidence_ledger import EvidenceStatus

    try:
        return EvidenceStatus(str(value or EvidenceStatus.AVAILABLE.value))
    except ValueError:
        return EvidenceStatus.UNAVAILABLE


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _append_claims(
    result: dict[str, Any],
    claim_fields: list[str] | None,
    records: list[dict[str, Any]],
    claim_subject: str | None = None,
) -> None:
    """Create field-bound claims only for facts present in the same evidence."""
    if not claim_fields or not records:
        return
    record = records[0]
    facts = record.get("facts") or {}
    claims = result.setdefault("claims", [])
    if not isinstance(claims, list):
        claims = []
        result["claims"] = claims
    subject = str(
        claim_subject
        or result.get("company_name")
        or result.get("supplier_name")
        or result.get("supplier_reference")
        or "企业"
    )
    for path in claim_fields:
        value = _read_path(facts, path)
        if value is _MISSING or value is None:
            continue
        claims.append({
            "claim_id": f"{record['evidence_id']}:claim:{path}",
            "entity_id": record["entity_id"],
            "dimension": record["dimension"],
            "statement": _claim_statement(subject, path, value),
            "value": value,
            "fact_path": path,
            "operator": "eq",
            "evidence_refs": [record["evidence_id"]],
            "confidence": 0.85,
        })


_CLAIM_LABELS = {
    "risk_score": "综合风险评分",
    "risk_level": "风险等级",
    "revenue_growth": "营业收入同比增长率",
    "net_profit_growth": "净利润同比增长率",
    "debt_ratio": "资产负债率",
    "cash_flow": "每股经营现金流",
    "roe": "净资产收益率",
    "net_profit_margin": "净利率",
    "current_ratio": "流动比率",
    "quick_ratio": "速动比率",
    "exposure_level": "内部采购敞口等级",
    "settlement_share": "同月实结算金额占比",
    "latest_actual_settlement_amount": "最新月实结算金额",
    "latest_received_record_count": "最新月收货记录数",
    "comparison_supplier_count": "同月可比较供应商数量",
    "missing_month_count": "最近 12 个月缺失交易月份数",
    "settlement_change_ratio": "最新月实结算金额环比变化",
    "receipt_record_change_ratio": "最新月收货记录数环比变化",
    "settlement_without_receipts_month_count": "结算与收货记录不一致月份数",
}
_PERCENTAGE_CLAIMS = {
    "revenue_growth", "net_profit_growth", "debt_ratio", "roe", "net_profit_margin",
    "settlement_change_ratio", "receipt_record_change_ratio",
}


def _claim_statement(subject: str, path: str, value: Any) -> str:
    label = _CLAIM_LABELS.get(path, path)
    return f"{subject} {label}：{_format_claim_value(path, value)}"


def _format_claim_value(path: str, value: Any) -> str:
    if path in _PERCENTAGE_CLAIMS and isinstance(value, (int, float)):
        return f"{value * 100:.1f}%"
    if path == "settlement_share" and isinstance(value, (int, float)):
        return "<0.1%" if 0 < value < 0.001 else f"{value * 100:.1f}%"
    if path == "latest_actual_settlement_amount" and isinstance(value, (int, float)):
        return f"{value:,.2f} 元"
    if path == "latest_received_record_count" and isinstance(value, (int, float)):
        return f"{value:,.0f} 条"
    if path == "comparison_supplier_count" and isinstance(value, (int, float)):
        return f"{value:,.0f} 家"
    if path in {"missing_month_count", "settlement_without_receipts_month_count"} and isinstance(value, (int, float)):
        return f"{value:,.0f} 个月"
    if path in {"current_ratio", "quick_ratio"} and isinstance(value, (int, float)):
        return f"{value:.2f}"
    if path == "cash_flow" and isinstance(value, (int, float)):
        return f"{value:.2f} 元/股"
    if path == "exposure_level":
        return {"high": "高敞口", "medium": "中敞口", "low": "低敞口", "unknown": "暂无法判断"}.get(
            str(value), str(value)
        )
    if path == "risk_score" and isinstance(value, (int, float)):
        return f"{value:g}/100"
    return str(value)


_MISSING = object()


def _read_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


__all__ = ["attach_tool_evidence"]
