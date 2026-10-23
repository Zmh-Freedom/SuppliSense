"""Normalize domain-tool observations into the common evidence envelope."""

from __future__ import annotations

import hashlib
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


def attach_screening_evidence(analysis: dict[str, Any], *, entity_id: str) -> dict[str, Any]:
    """Map screening source states and matches into auditable tool evidence.

    Unknown screening states deliberately omit clean and zero-match claims.
    Source observation, collection, expiry, and scope stay attached to the
    corresponding source evidence record rather than becoming one generic
    tool-call timestamp.
    """
    from app.graphs.agent_core.evidence_ledger import EvidenceStatus, build_evidence_record
    from app.domains.risk.sanctions_service import project_legacy_sanctions

    status = str(analysis.get("screening_status") or "unavailable")
    coverage = analysis.get("coverage") if isinstance(analysis.get("coverage"), dict) else {}
    coverage_status = str(coverage.get("status") or "insufficient")
    company_name = str(analysis.get("company_name") or "")
    stable_entity_id = str(analysis.get("entity_id") or entity_id or "")
    matches = [item for item in analysis.get("matches", []) if isinstance(item, dict)]
    current_matches = [
        item for item in matches
        if (item.get("validity") or {}).get("status") == "available"
    ]
    clean: bool | None = True if status == "no_match_in_scope" else False if status == "confirmed_match" else None
    projection: dict[str, Any] = {}
    if status in {"no_match_in_scope", "confirmed_match"}:
        try:
            projection = project_legacy_sanctions(analysis)
        except ValueError:
            projection = {}

    tool_status = (
        "success"
        if coverage_status == "complete" and status in {"no_match_in_scope", "confirmed_match"}
        else "partial"
        if current_matches or any(
            ((item.get("validity") or {}).get("status")) in {"available", "empty"}
            for item in (coverage.get("sources") or []) if isinstance(item, dict)
        )
        else "unavailable"
    )
    result: dict[str, Any] = {
        "company_name": company_name,
        "sanctions_score": projection.get("sanctions_score"),
        "sanctions_level": projection.get("sanctions_level"),
        "match_count": len(current_matches),
        "matches": matches,
        "clean": clean,
        "status": tool_status,
        "evidence_refs": [],
        "evidence_records": [],
        "claims": [],
    }

    source_records: dict[str, dict[str, Any]] = {}
    evidence_refs: list[str] = []
    required_sources = {
        str(item.get("source") if isinstance(item, dict) else item)
        for item in coverage.get("required_sources", [])
    }
    source_entries = [item for item in coverage.get("sources", []) if isinstance(item, dict)]
    limitations = [str(item) for item in analysis.get("limitations", []) if str(item).strip()]
    for index, entry in enumerate(source_entries):
        source = str(entry.get("source") or "sanctions_source")
        evidence = entry.get("evidence") if isinstance(entry.get("evidence"), dict) else {}
        validity = entry.get("validity") if isinstance(entry.get("validity"), dict) else {}
        state = str(validity.get("status") or evidence.get("status") or "unknown")
        evidence_status = _screening_evidence_status(state)
        source_matches = [item for item in matches if str(item.get("source") or "") == source]
        current_source_matches = [
            item for item in source_matches
            if (item.get("validity") or {}).get("status") == "available"
        ]
        checked_at = _screening_datetime(analysis.get("checked_at")) or datetime.now(timezone.utc)
        collected_at = _screening_datetime(evidence.get("collected_at")) or checked_at
        observed_at = evidence.get("observed_at") or validity.get("observed_at")
        expires_at = validity.get("expires_at") or evidence.get("expires_at")
        source_version = evidence.get("source_version") or entry.get("version")
        digest = hashlib.sha256(
            f"{stable_entity_id}:{source}:{source_version}:{index}".encode()
        ).hexdigest()[:24]
        record_id = f"screening:{digest}:source"
        facts = {
            "source": source,
            "source_version": source_version,
            "scope": entry.get("scope") or evidence.get("scope"),
            "observed_at": observed_at,
            "collected_at": evidence.get("collected_at") or validity.get("collected_at"),
            "expires_at": expires_at,
            "query_receipt": evidence.get("query_receipt") or {},
            "validity_status": state,
            "screening_status": status,
            "coverage_status": coverage_status,
            "match_count": len(current_source_matches),
            "limitations": limitations,
        }
        if status == "no_match_in_scope" and source in required_sources and state in {"available", "empty"}:
            facts["clean"] = True
        record = build_evidence_record(
            evidence_id=record_id,
            entity_id=stable_entity_id,
            dimension="compliance",
            provider=source[:128],
            source_type="sanctions_list",
            payload=facts,
            status=evidence_status,
            collected_at=collected_at,
            valid_until=_screening_datetime(expires_at),
            data_mode="synthetic" if evidence.get("source_mode") in {"sample", "synthetic"} else "formal",
        ).model_dump(mode="json")
        result["evidence_records"].append(record)
        evidence_refs.append(record_id)
        source_records[source] = {"record_id": record_id, "status": state, "entry": entry}

        for match_index, match in enumerate(source_matches):
            match_digest = hashlib.sha256(
                f"{record_id}:{match.get('record_id')}:{match.get('record_name')}:{match_index}".encode()
            ).hexdigest()[:20]
            match_record_id = f"screening:{digest}:match:{match_digest}"
            match_facts = {
                **facts,
                "record_id": match.get("record_id"),
                "record_name": match.get("record_name"),
                "match_type": match.get("match_type"),
                "matched_on": match.get("matched_on"),
            }
            match_record = build_evidence_record(
                evidence_id=match_record_id,
                entity_id=stable_entity_id,
                dimension="compliance",
                provider=source[:128],
                source_type="sanctions_list_match",
                payload=match_facts,
                status=_screening_evidence_status((match.get("validity") or {}).get("status") or state),
                collected_at=collected_at,
                valid_until=_screening_datetime(expires_at),
                data_mode="synthetic" if evidence.get("source_mode") in {"sample", "synthetic"} else "formal",
            ).model_dump(mode="json")
            result["evidence_records"].append(match_record)
            evidence_refs.append(match_record_id)
            if (match.get("validity") or {}).get("status") != "available" or match.get("match_type") not in {"confirmed", "possible"}:
                continue
            confirmed = match.get("match_type") == "confirmed"
            record_name = str(match.get("record_name") or "名单记录")
            statement = (
                f"在 {source} 中发现与当前主体标识一致的名单记录：{record_name}。"
                if confirmed
                else f"在 {source} 中发现名称相似名单线索：{record_name}；主体尚未确认。"
            )
            result["claims"].append({
                "claim_id": f"{match_record_id}:claim:record_name",
                "entity_id": stable_entity_id,
                "dimension": "compliance",
                "statement": statement,
                "value": record_name,
                "fact_path": "record_name",
                "operator": "eq",
                "evidence_refs": [match_record_id],
                "confidence": 0.95 if confirmed else 0.65,
            })

    domestic_findings = [
        item for item in analysis.get("domestic_findings", []) if isinstance(item, dict)
    ]
    for index, finding in enumerate(domestic_findings):
        validity = finding.get("validity") if isinstance(finding.get("validity"), dict) else {}
        source = str(finding.get("source") or "domestic_compliance_source")
        state = str(validity.get("status") or "unknown")
        checked_at = _screening_datetime(analysis.get("checked_at")) or datetime.now(timezone.utc)
        observed_at = validity.get("observed_at")
        collected_at = _screening_datetime(validity.get("collected_at")) or checked_at
        expires_at = validity.get("expires_at")
        digest = hashlib.sha256(
            f"{stable_entity_id}:{source}:{finding.get('kind')}:{index}".encode()
        ).hexdigest()[:24]
        record_id = f"screening:{digest}:domestic"
        facts = {
            "domestic_finding_kind": finding.get("kind"),
            "count": finding.get("count"),
            "source": source,
            "source_version": validity.get("source_version"),
            "scope": analysis.get("scope"),
            "observed_at": observed_at,
            "collected_at": validity.get("collected_at"),
            "expires_at": expires_at,
            "query_receipt": validity.get("query_receipt") or {},
            "validity_status": state,
            "screening_status": status,
            "limitations": limitations,
        }
        record = build_evidence_record(
            evidence_id=record_id,
            entity_id=stable_entity_id,
            dimension="compliance",
            provider=source[:128],
            source_type="domestic_compliance",
            payload=facts,
            status=_screening_evidence_status(state),
            collected_at=collected_at,
            valid_until=_screening_datetime(expires_at),
        ).model_dump(mode="json")
        result["evidence_records"].append(record)
        evidence_refs.append(record_id)
        count = finding.get("count")
        if state not in {"available", "empty"} or isinstance(count, bool) or not isinstance(count, int):
            continue
        result["claims"].append({
            "claim_id": f"{record_id}:claim:count",
            "entity_id": stable_entity_id,
            "dimension": "compliance",
            "statement": f"国内 {finding.get('kind') or '风险'} 来源核验记录数：{count}。",
            "value": count,
            "fact_path": "count",
            "operator": "eq",
            "evidence_refs": [record_id],
            "confidence": 0.9,
        })

    if status == "no_match_in_scope":
        required_refs = [
            source_records[source]["record_id"]
            for source in required_sources
            if source in source_records and source_records[source]["status"] in {"available", "empty"}
        ]
        if required_sources and len(required_refs) == len(required_sources):
            result["claims"].append({
                "claim_id": f"screening:{stable_entity_id}:claim:clean",
                "entity_id": stable_entity_id,
                "dimension": "compliance",
                "statement": f"在已核验的名单范围内，{company_name} 未发现匹配记录。",
                "value": True,
                "fact_path": "clean",
                "operator": "eq",
                "evidence_refs": required_refs,
                "confidence": 0.9,
            })

    if not result["evidence_records"] and stable_entity_id:
        checked_at = _screening_datetime(analysis.get("checked_at")) or datetime.now(timezone.utc)
        digest = hashlib.sha256(f"{stable_entity_id}:{status}:{checked_at.isoformat()}".encode()).hexdigest()[:24]
        record_id = f"screening:{digest}:query"
        record = build_evidence_record(
            evidence_id=record_id,
            entity_id=stable_entity_id,
            dimension="compliance",
            provider="sanctions_service",
            source_type="screening_query",
            payload={
                "screening_status": status,
                "coverage_status": coverage_status,
                "scope": analysis.get("scope"),
                "limitations": limitations,
                "observed_at": None,
                "expires_at": None,
            },
            status=EvidenceStatus.UNAVAILABLE,
            collected_at=checked_at,
        ).model_dump(mode="json")
        result["evidence_records"].append(record)
        evidence_refs.append(record_id)
    result["evidence_refs"] = evidence_refs
    return result


def _screening_evidence_status(value: Any):
    from app.graphs.agent_core.evidence_ledger import EvidenceStatus

    return {
        "available": EvidenceStatus.AVAILABLE,
        "empty": EvidenceStatus.CONFIRMED_EMPTY,
        "missing": EvidenceStatus.MISSING,
        "stale": EvidenceStatus.STALE,
        "conflicting": EvidenceStatus.CONFLICTING,
    }.get(str(value), EvidenceStatus.UNAVAILABLE)


def _screening_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


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
            **({"unit": "ratio"} if path in {"quality_rate", "on_time_rate"} else {}),
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
    "quality_rate": "合格率",
    "on_time_rate": "准时交付率",
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
    "risk_detail.lawsuit_count": "诉讼记录数",
    "risk_detail.executed_count": "被执行记录数",
    "risk_detail.dishonesty_count": "失信记录数",
    "risk_detail.major_lawsuit": "重大诉讼标记",
    "risk_detail.abnormal_operation_count": "经营异常记录数",
    "risk_detail.administrative_penalty_count": "行政处罚记录数",
    "risk_detail.legal_person_change_frequent": "法人频繁变更",
    "risk_detail.guarantee_count": "对外担保记录数",
    "risk_detail.pledge_count": "股权质押记录数",
    "risk_detail.bankruptcy_count": "破产相关记录数",
    "risk_detail.env_penalty_count": "环保处罚记录数",
    "risk_detail.data_coverage.coverage_ratio": "风险数据覆盖率",
    "risk_detail.data_coverage.assessment_status": "风险数据覆盖状态",
    "overall_sentiment": "舆情总体倾向",
    "sentiment_score": "舆情倾向评分",
    "articles_count": "纳入分析的新闻数",
    "negative_count": "负面新闻数",
    "neutral_count": "中性新闻数",
    "positive_count": "正面新闻数",
    "summary": "舆情摘要",
    "probability": "未来 6-12 个月风险恶化概率",
    "label": "风险预测结论",
    "warning_score": "风险预警分数",
    "max_score": "风险预测满分",
    "prediction_signal_summary": "风险预测信号",
    "has_data": "预测数据可用",
    "related_count": "关联主体数量",
    "branch_count": "分支机构数量",
    "dependency_count": "供应链依赖数量",
    "same_industry_count": "同行业关联数量",
    "high_risk_related_count": "高风险关联主体数量",
    "trend": "风险趋势",
    "period_months": "趋势观察周期（月）",
    "alternatives_count": "替代供应商数量",
    "source_industry": "目标企业所属行业",
    "source_risk_score": "目标企业当前安全评分",
    "format": "报告格式",
    "length": "报告字符数",
    "size_bytes": "报告文件大小（字节）",
    "count": "企业数量",
}
_PERCENTAGE_CLAIMS = {
    "quality_rate", "on_time_rate",
    "revenue_growth", "net_profit_growth", "debt_ratio", "roe", "net_profit_margin",
    "settlement_change_ratio", "receipt_record_change_ratio",
    "risk_detail.data_coverage.coverage_ratio",
}


def _claim_statement(subject: str, path: str, value: Any) -> str:
    label = _CLAIM_LABELS.get(path, path)
    return f"{subject} {label}：{_format_claim_value(path, value)}"


def _format_claim_value(path: str, value: Any) -> str:
    if path == "probability":
        return {
            "high": "高概率恶化",
            "medium": "可能恶化",
            "low": "大概率稳定",
            "unknown": "当前未覆盖",
        }.get(str(value), str(value))
    if path in _PERCENTAGE_CLAIMS and isinstance(value, (int, float)):
        return f"{value * 100:.1f}%"
    if path == "risk_detail.major_lawsuit" and isinstance(value, bool):
        return "有" if value else "无"
    if path == "risk_detail.legal_person_change_frequent" and isinstance(value, bool):
        return "是" if value else "否"
    if path.startswith("risk_detail.") and path.endswith("_count") and isinstance(value, (int, float)):
        return f"{value:,.0f} 条"
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


def attach_operational_metric_evidence(analysis: dict, *, entity_id: str, dimension: str) -> dict:
    """Bind each operational observation without altering domain/API payloads."""
    from app.domains.risk.assessment_contracts import canonical_hash
    from app.domains.risk.operational_metrics import operational_observation_status
    from app.graphs.agent_core.evidence_ledger import EvidenceStatus, build_evidence_record

    result = dict(analysis['payload'])
    metric_result = analysis['metric_result']
    if metric_result is None or not metric_result.observations:
        return attach_tool_evidence(result, tool_name='assess_operational_risk',
                                    entity_id=entity_id, dimension=dimension)
    entity_id = _active_entity_id(entity_id)
    now = datetime.now(timezone.utc)
    observations = metric_result.observations
    aggregate = metric_result.status == 'available' and metric_result.value is not None and len(observations) > 1
    groups = ([(list(observations), True)] if aggregate else []) + [([item], False) for item in observations]
    records = []
    for group, is_aggregate in groups:
        item = group[0]
        raw = [observation.model_dump(mode='json') for observation in group]
        digest = canonical_hash(raw)
        facts = {
            'company_name': result.get('company_name'),
            'period': item.period, 'scope': item.scope, 'source_version': item.source_version,
            'observed_at': min((observation.observed_at.isoformat() for observation in group if observation.observed_at), default=None),
            'source_observations': raw, 'limitations': analysis['limitations'],
        }
        value = metric_result.value if is_aggregate else item.value
        if value is not None:
            facts[item.metric] = value
            facts[f'{item.metric}_unit'] = 'ratio'
        states = [operational_observation_status(observation, now) for observation in group]
        if 'conflicting' in states or metric_result.status == 'conflicting':
            status = EvidenceStatus.CONFLICTING
        elif 'unavailable' in states:
            status = EvidenceStatus.UNAVAILABLE
        elif 'stale' in states:
            status = EvidenceStatus.STALE
        else:
            status = EvidenceStatus.AVAILABLE
        valid_until = min((observation.valid_until for observation in group if observation.valid_until), default=None)
        record = build_evidence_record(
            evidence_id=f'operational:{entity_id}:{dimension}:{digest}', entity_id=entity_id, dimension=dimension,
            provider='assess_operational_risk', source_type='feishu_transaction_snapshot',
            payload=facts, status=status, collected_at=now, valid_until=valid_until,
            data_mode='formal', query={'scope': item.scope, 'period': item.period},
            raw_payload_ref=f'operational-source:{item.source}:{digest}',
        ).model_dump(mode='json')
        records.append(record)
        scope_labels = {'supplier_code': '供应商代码', 'material_code': '物料', 'base': '基地', 'purchasing_org_code': '采购组织'}
        scope_text = '；'.join(f"{scope_labels.get(key, '范围')} {value}" for key, value in item.scope.items())
        kind = f"汇总{len(group)}条来源" if is_aggregate else f"来源记录 {item.source_record_id or '待核验'}"
        subject = f"{result.get('company_name') or '企业'}（{item.period}；{scope_text}；{kind}）"
        _append_claims(result, [item.metric], [record], subject)
    result['evidence_records'] = records
    return attach_tool_evidence(result, tool_name='assess_operational_risk', entity_id=entity_id, dimension=dimension)
