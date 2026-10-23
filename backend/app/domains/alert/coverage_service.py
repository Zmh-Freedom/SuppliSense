"""Current evidence coverage adapters for monitoring and intake views."""

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from app.domains.risk import assessment_service
from app.domains.risk.assessment_contracts import get_assessment_context
from app.domains.risk.assessment_policy import current_policy
from app.domains.risk.evidence_validity import evaluate_evidence_validity


SENTIMENT_FRESHNESS = timedelta(hours=6)
SENTIMENT_POLICY_VERSION = "sentiment-cache-6h-v1"
SENTIMENT_SCOPE = "exact_company_name"
TRANSACTION_FRESHNESS_POLICY: dict | None = None
MONITOR_DIMENSIONS = (
    ("identity", "主体身份"),
    ("risk_snapshot", "风险快照"),
    ("financial", "财务/公开信息"),
    ("transaction", "内部交易"),
    ("sentiment", "舆情"),
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _state(
    source: str,
    status: str,
    reason_code: str,
    detail: str,
    *,
    facts: dict | None = None,
) -> dict:
    return {
        "source": source,
        "status": status,
        "reason_codes": [reason_code],
        "detail": detail,
        "facts": facts or {},
    }


def _dimension(key: str, label: str, validity: dict, *, detail: str | None = None, facts: dict | None = None) -> dict:
    validity_status = validity.get("status")
    status = (
        "available"
        if validity_status in {"available", "empty"}
        else "missing"
        if validity_status == "missing"
        else "pending"
    )
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail or validity.get("detail") or "当前状态待核验。",
        "validity": validity,
        "facts": facts or validity.get("facts") or {},
    }


def _manifest_validity(row: dict, dimension: str, entry: dict, *, now: datetime) -> dict:
    identity = row.get("identity") or {}
    policy = row.get("policy_snapshot") or {}
    raw_status = entry.get("status")
    status = raw_status if raw_status in {"available", "empty", "missing", "stale", "failed", "conflicting", "unknown", "not_queried"} else "unknown"
    if raw_status in {"unavailable", "not_requested"}:
        status = "unknown"
    content_identity = str(entry.get("content_hash") or entry.get("evidence_id") or "missing-content-id")
    policy_identity = str(row.get("policy_hash") or policy.get("version") or "missing-policy-id")
    source_evidence = {
        "source": entry.get("source") or "assessment_manifest",
        "source_version": f"frozen-assessment:{policy_identity}:{content_identity}",
        "source_version_provenance": "frozen policy hash and evidence content hash",
        "entity_id": str(row.get("company_id") or identity.get("company_id") or ""),
        "scope": str(row.get("scope_key") or ""),
        "source_mode": "formal",
        "status": status,
        "observed_at": entry.get("observed_at"),
        "collected_at": entry.get("collected_at"),
        "expires_at": entry.get("expires_at"),
        "query_receipt": entry.get("query_receipt") or {},
        "coverage_partial": bool(entry.get("coverage_partial")),
        # Assessment manifests use the string "none" for a clean result.
        # Treat only an explicit conflict signal as a conflict; generic truthiness
        # would incorrectly make every non-empty status string conflicting.
        "conflict": entry.get("conflict") is True or entry.get("conflict") == "conflicting",
    }
    freshness_days = (policy.get("freshness_days") or {}).get(dimension)
    max_age = timedelta(days=float(freshness_days)) if isinstance(freshness_days, (int, float)) and freshness_days > 0 else None
    return evaluate_evidence_validity(
        source_evidence,
        expected_entity_id=str(row.get("company_id") or identity.get("company_id") or ""),
        expected_scope=str(row.get("scope_key") or ""),
        now=now,
        max_age=max_age,
        policy_version=str(policy.get("version") or "") if max_age else None,
    )


def _assessment_dimension_states(row: dict, *, now: datetime) -> tuple[dict[str, dict], dict]:
    policy = row.get("policy_snapshot") or {}
    identity = row.get("identity") or {}
    required = list(identity.get("dimensions") or policy.get("required_dimensions") or [])
    manifest = row.get("evidence_manifest") or []
    states: dict[str, dict] = {}
    for dimension in required:
        entries = [item for item in manifest if item.get("dimension") == dimension]
        if not entries:
            states[dimension] = _state(
                "assessment_manifest",
                "missing",
                "ASSESSMENT_EVIDENCE_MISSING",
                f"冻结评估缺少{dimension}来源记录。",
            )
            continue
        validity_records = [_manifest_validity(row, dimension, entry, now=now) for entry in entries]
        source_states = {item["status"] for item in validity_records}
        if "conflicting" in source_states:
            status = "conflicting"
        elif "failed" in source_states:
            status = "failed"
        elif "stale" in source_states:
            status = "stale"
        elif source_states <= {"available", "empty"}:
            status = "available" if "available" in source_states else "empty"
        elif "missing" in source_states:
            status = "missing"
        else:
            status = "unknown"
        states[dimension] = {
            **validity_records[0],
            "status": status,
            "reason_codes": list(dict.fromkeys(code for item in validity_records for code in item.get("reason_codes", []))),
            "detail": "；".join(dict.fromkeys(item.get("detail", "") for item in validity_records if item.get("detail"))),
            "sources": validity_records,
        }
    return states, {"required_dimensions": required, "manifest_count": len(manifest)}


def _identity_for_target(target: dict, *, actor_id: str, actor_role: str) -> dict:
    return assessment_service.resolve_identity(
        str(target.get("company_name") or ""),
        company_id=str(target.get("company_id") or "") or None,
        monitor_target_id=str(target.get("monitor_target_id") or "") or None,
        actor_id=actor_id,
        actor_role=actor_role,
        dimensions=list(current_policy()["required_dimensions"]),
        purchase_scope=str(target.get("purchase_scope") or ""),
    )


def build_monitor_coverage(
    db: object,
    target: dict,
    snapshot: dict | None,
    *,
    now: datetime,
) -> dict:
    """Build the legacy seven-field coverage response from current evidence."""
    checked_at = _utc(now)
    context = get_assessment_context()
    actor_id = str(context.get("actor_id") or "")
    actor_role = str(context.get("actor_role") or "")
    assessment_row = None
    resolved_identity = None
    assessment_error = False

    if actor_id and actor_role:
        if snapshot and snapshot.get("assessment_id"):
            try:
                assessment_row = assessment_service.get_monitor_assessment_context(
                    target,
                    snapshot,
                    actor_id=actor_id,
                    actor_role=actor_role,
                )
            except Exception:
                assessment_error = True
            if assessment_row:
                resolved_identity = assessment_row.get("resolved_identity") or {}
        elif snapshot and snapshot.get("_assessment_context_unavailable"):
            assessment_error = True
        else:
            try:
                resolved_identity = _identity_for_target(
                    target, actor_id=actor_id, actor_role=actor_role
                )
            except Exception:
                assessment_error = True

    identity_matches_target = bool(
        resolved_identity
        and target.get("identity_status") == "verified"
        and str(resolved_identity.get("company_id") or "")
        == str(target.get("company_id") or resolved_identity.get("company_id") or "")
        and str(resolved_identity.get("monitor_target_id") or target.get("monitor_target_id") or "")
        == str(target.get("monitor_target_id") or "")
    )
    identity_validity = (
        _state("verified_company_binding", "available", "IDENTITY_REVALIDATED", "当前主体与监控对象绑定已重新核验。")
        if identity_matches_target
        else _state(
            "verified_company_binding",
            "failed" if assessment_error else "unknown",
            "IDENTITY_SCOPE_UNAVAILABLE" if assessment_error else "IDENTITY_NOT_REVALIDATED",
            "当前身份或责任范围未能重新核验。",
        )
    )

    dimensions = [_dimension("identity", "主体身份", identity_validity)]
    assessment_states: dict[str, dict] = {}
    assessment_metadata: dict = {}
    if assessment_row:
        assessment_states, assessment_metadata = _assessment_dimension_states(
            assessment_row, now=checked_at
        )
        required_dimensions = assessment_metadata["required_dimensions"]
        current_source_states = [assessment_states.get(key, {}).get("status", "missing") for key in required_dimensions]
        risk_validity_status = (
            "available"
            if assessment_row.get("quality") == "complete"
            and current_source_states
            and all(status in {"available", "empty"} for status in current_source_states)
            else "conflicting"
            if "conflicting" in current_source_states
            else "failed"
            if "failed" in current_source_states
            else "stale"
            if "stale" in current_source_states
            else "unknown"
        )
        risk_validity = {
            "source": "frozen_assessment",
            "status": risk_validity_status,
            "reason_codes": ["ASSESSMENT_CURRENTLY_VALID"] if risk_validity_status == "available" else ["ASSESSMENT_NOT_CURRENTLY_COMPLETE"],
            "detail": "冻结评估的必需来源当前有效且评估质量完整。" if risk_validity_status == "available" else "历史风险快照仍可查看，但证据过期、部分或不可用，当前需要重新核验。",
            "facts": {"assessment_id": assessment_row.get("id"), "assessment_quality": assessment_row.get("quality"), "dimensions": assessment_states},
        }
    elif snapshot and snapshot.get("assessment_id") and assessment_error:
        risk_validity = _state("frozen_assessment", "failed", "ASSESSMENT_STORE_UNAVAILABLE", "冻结评估记录读取失败，未用 Mongo 同名缓存替代当前证据。")
    elif snapshot:
        risk_validity = _state("legacy_snapshot", "unknown", "FROZEN_MANIFEST_UNAVAILABLE", "历史快照缺少可重新核验的 PostgreSQL 冻结清单。")
    else:
        risk_validity = _state("frozen_assessment", "missing", "ASSESSMENT_MISSING", "尚未完成风险评估。")
    risk_facts = {
        "assessment_id": snapshot.get("assessment_id") if snapshot else None,
        "risk_score": snapshot.get("risk_score") if snapshot else None,
        "risk_level": snapshot.get("risk_level") if snapshot else None,
        "checked_at": snapshot.get("checked_at") if snapshot else None,
    }
    dimensions.append(
        _dimension(
            "risk_snapshot",
            "风险快照",
            risk_validity,
            detail=(
                risk_validity["detail"]
                + (f" 历史评估时间：{risk_facts['checked_at']}。" if risk_facts["checked_at"] else "")
            ),
            facts=risk_facts,
        )
    )

    financial_validity = assessment_states.get("financial")
    if financial_validity is None:
        financial_validity = (
            _state("financial_assessment", "unknown", "FINANCIAL_MANIFEST_UNAVAILABLE", "历史财务数字没有当前可核验的冻结来源。")
            if snapshot and (snapshot.get("financial") or (snapshot.get("risk_detail") or {}).get("data_coverage", {}).get("available_dimensions"))
            else _state("financial_assessment", "missing", "FINANCIAL_SOURCE_MISSING", "当前没有可核验的财务来源。")
        )
    dimensions.append(_dimension("financial", "财务/公开信息", financial_validity))

    if identity_matches_target:
        source_identity = {
            **resolved_identity,
            "company_name": resolved_identity.get("company_name") or target.get("company_name"),
            "company_id": resolved_identity.get("company_id") or target.get("company_id"),
            "supplier_id": resolved_identity.get("supplier_id") or target.get("supplier_id"),
            "supplier_code": resolved_identity.get("supplier_code") or target.get("supplier_code"),
            "monitor_target_id": target.get("monitor_target_id"),
        }
        try:
            transaction_validity = read_transaction_validity(db, source_identity, now=checked_at)
        except Exception:
            transaction_validity = _state("supplier_transaction_snapshots", "failed", "TRANSACTION_READ_FAILED", "内部交易来源读取失败。")
        try:
            sentiment_validity = read_sentiment_validity(db, source_identity, now=checked_at)
        except Exception:
            sentiment_validity = _state("sentiment_results", "failed", "SENTIMENT_READ_FAILED", "舆情来源读取失败。")
    else:
        transaction_validity = _state("supplier_transaction_snapshots", "unknown", "IDENTITY_SCOPE_UNAVAILABLE", "主体未重新核验，未读取内部交易数据。")
        sentiment_validity = _state("sentiment_results", "unknown", "IDENTITY_SCOPE_UNAVAILABLE", "主体未重新核验，未读取舆情数据。")
    dimensions.append(_dimension("transaction", "内部交易", transaction_validity))
    dimensions.append(_dimension("sentiment", "舆情", sentiment_validity))

    available_count = sum(item["status"] == "available" for item in dimensions)
    total_count = len(MONITOR_DIMENSIONS)
    status = "complete" if available_count == total_count else "partial" if available_count else "insufficient"
    return {
        "status": status,
        "available_count": available_count,
        "total_count": total_count,
        "coverage_ratio": round(available_count / total_count, 2) if total_count else 0.0,
        "summary": f"{available_count}/{total_count} 个数据域可用",
        "dimensions": dimensions,
        "missing_dimensions": [item["label"] for item in dimensions if item["status"] != "available"],
    }


def _transaction_query(identity: dict) -> dict | None:
    supplier_code = identity.get("supplier_code")
    supplier_id = identity.get("supplier_id")
    if supplier_code:
        return {"supplier_code": supplier_code}
    if supplier_id:
        return {"supplier_id": supplier_id}
    return None


def read_transaction_validity(db: object, identity: dict, *, now: datetime) -> dict:
    """Read the latest transaction period; no accepted expiry policy means pending."""
    source = "supplier_transaction_snapshots"
    query = _transaction_query(identity)
    if not query:
        return _state(source, "missing", "TRANSACTION_IDENTITY_MISSING", "没有稳定供应商标识，当前无法读取内部交易来源。")
    try:
        rows = list(db[source].find(query))
    except Exception:
        return _state(source, "failed", "TRANSACTION_READ_FAILED", "内部交易来源读取失败。")
    if not rows:
        return _state(source, "missing", "TRANSACTION_SOURCE_MISSING", "当前没有该供应商的内部交易快照。")

    month_values = [str(row.get("snapshot_month") or "") for row in rows]
    valid_months = [value for value in month_values if re.fullmatch(r"[1-9]\d{3}-(0[1-9]|1[0-2])", value)]
    facts = {"months": sorted(set(valid_months)), "row_count": len(rows)}
    if not valid_months or len(valid_months) != len(month_values):
        return _state(source, "unknown", "TRANSACTION_PERIOD_UNKNOWN", "交易快照缺少有效月份，当前覆盖待核验。", facts=facts)
    latest_month = max(valid_months)
    latest_rows = [row for row in rows if str(row.get("snapshot_month")) == latest_month]
    facts["snapshot_month"] = latest_month
    facts["row_count"] = len(latest_rows)
    if any(row.get("sync_status") != "current" for row in latest_rows):
        return _state(source, "failed", "LATEST_TRANSACTION_SYNC_FAILED", "最新交易月份同步失败或未完成，没有回退到较早月份。", facts=facts)
    if any(row.get("data_mode") != "real" for row in latest_rows):
        return _state(source, "unknown", "SYNTHETIC_TRANSACTION_DATA", "最新交易月份含演示或未知数据模式，不能计入正式覆盖。", facts=facts)
    if any(
        row.get("eligible_for_formal_assessment") is not True
        or row.get("data_quality_status") != "valid"
        or row.get("validation_errors")
        or row.get("data_quality_issues")
        for row in latest_rows
    ):
        return _state(source, "unknown", "TRANSACTION_NOT_ELIGIBLE", "最新交易月份未通过正式评估资格校验。", facts=facts)
    if TRANSACTION_FRESHNESS_POLICY is None:
        return _state(source, "unknown", "TRANSACTION_FRESHNESS_POLICY_MISSING", "交易来源没有已验收的有效期政策，当前覆盖待核验。", facts=facts)

    policy = TRANSACTION_FRESHNESS_POLICY
    observation = latest_rows[0].get(policy.get("observed_at_field", "observed_at"))
    collected = latest_rows[0].get(policy.get("collected_at_field", "synced_at"))
    expiry = latest_rows[0].get(policy.get("expires_at_field", "expires_at"))
    evidence = {
        "source": source,
        "source_version": latest_rows[0].get(policy.get("version_field", "source_version")),
        "entity_id": str(identity.get("company_id") or identity.get("supplier_id") or ""),
        "scope": str(identity.get("scope") or "supplier_transactions"),
        "source_mode": "formal",
        "status": "available",
        "observed_at": observation,
        "collected_at": collected,
        "expires_at": expiry,
        "query_receipt": {"status": "success", "record_count": len(latest_rows)},
        "coverage_partial": False,
        "conflict": False,
    }
    return evaluate_evidence_validity(
        evidence,
        expected_entity_id=evidence["entity_id"],
        expected_scope=evidence["scope"],
        now=now,
        max_age=policy.get("max_age"),
        policy_version=policy.get("version"),
    ) | {"facts": facts}


def read_sentiment_validity(db: object, identity: dict, *, now: datetime) -> dict:
    """Read the newest relevant sentiment result under the existing six-hour rule."""
    source = "sentiment_results"
    company_name = str(identity.get("company_name") or "")
    entity_id = str(identity.get("company_id") or identity.get("supplier_id") or "")
    if not company_name or not entity_id:
        return _state(source, "unknown", "SENTIMENT_IDENTITY_REQUIRED", "舆情主体没有稳定身份绑定，当前无法核验。")
    try:
        document = db[source].find_one({"company_name": company_name}, sort=[("analyzed_at", -1)])
    except Exception:
        return _state(source, "failed", "SENTIMENT_READ_FAILED", "舆情来源读取失败。")
    if not isinstance(document, dict):
        return _state(source, "missing", "SENTIMENT_SOURCE_MISSING", "当前没有舆情分析记录。")
    facts = {
        "analyzed_at": document.get("analyzed_at"),
        "articles_count": document.get("articles_count"),
        "summary": document.get("summary"),
    }
    if document.get("company_id") and str(document.get("company_id")) != entity_id:
        return _state(source, "conflicting", "SENTIMENT_ENTITY_MISMATCH", "舆情记录绑定的主体与当前供应商不一致。", facts=facts)
    articles = document.get("articles") or []
    if not articles:
        return _state(source, "unknown", "SENTIMENT_EMPTY_REQUIRES_REFRESH", "舆情缓存没有相关新闻，需要复查后才能确认当前状态。", facts=facts)
    try:
        from app.domains.risk.sentiment import _cached_sentiment_is_relevant

        relevant = _cached_sentiment_is_relevant(company_name, document)
    except Exception:
        relevant = False
    if not relevant:
        return _state(source, "unknown", "SENTIMENT_RELEVANCE_UNVERIFIED", "最新舆情结果未通过当前主体相关性检查。", facts=facts)

    evidence = {
        "source": source,
        "source_version": SENTIMENT_POLICY_VERSION,
        "entity_id": entity_id,
        "scope": SENTIMENT_SCOPE,
        "source_mode": "formal",
        "status": "available",
        "observed_at": document.get("analyzed_at"),
        "collected_at": document.get("collected_at") or document.get("analyzed_at"),
        "expires_at": document.get("expires_at"),
        "query_receipt": {"status": "success", "record_count": len(articles)},
        "coverage_partial": False,
        "conflict": False,
    }
    validity = evaluate_evidence_validity(
        evidence,
        expected_entity_id=entity_id,
        expected_scope=SENTIMENT_SCOPE,
        now=now,
        max_age=SENTIMENT_FRESHNESS,
        policy_version=SENTIMENT_POLICY_VERSION,
    )
    return {**validity, "facts": facts}


def _intake_enterprise_validity(enterprise_state: dict) -> dict:
    validity = enterprise_state.get("validity") if isinstance(enterprise_state, dict) else None
    if isinstance(validity, dict):
        return validity
    state = enterprise_state.get("status") if isinstance(enterprise_state, dict) else None
    if state in {"failed", "unavailable", "not_queried"}:
        return _state("enterprise_profile", "unknown", "ENTERPRISE_SOURCE_UNAVAILABLE", str(enterprise_state.get("detail") or "企业资料来源当前不可用。"))
    if state == "available":
        return _state("enterprise_profile", "unknown", "ENTERPRISE_VALIDITY_UNVERIFIED", "已有企业资料，但来源观察时间和有效范围未核验。")
    return _state("enterprise_profile", "missing", "ENTERPRISE_SOURCE_MISSING", "当前没有企业工商与风险资料。")


def build_intake_coverage(
    db: object,
    candidate: dict | None,
    query: str,
    enterprise_state: dict,
    *,
    now: datetime,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Build the same current-source states for the monitoring intake flow."""
    company_name = str((candidate or {}).get("legal_name") or query)
    supplier_id = (candidate or {}).get("supplier_id")
    supplier_code = (candidate or {}).get("supplier_code")
    company_id = (candidate or {}).get("company_id")
    verified = bool(candidate and candidate.get("verification_status") == "verified")
    identity = {
        "company_name": company_name,
        "company_id": company_id or supplier_id,
        "supplier_id": supplier_id,
        "supplier_code": supplier_code,
        "scope": "monitor_intake",
    }
    if verified and (supplier_id or supplier_code):
        transaction = read_transaction_validity(db, identity, now=now)
    else:
        transaction = _state("supplier_transaction_snapshots", "unknown", "IDENTITY_SCOPE_UNAVAILABLE", "候选主体尚未绑定已核验供应商，未读取内部交易。")
    if verified and identity["company_id"]:
        sentiment = read_sentiment_validity(db, identity, now=now)
    else:
        sentiment = _state("sentiment_results", "unknown", "IDENTITY_SCOPE_UNAVAILABLE", "候选主体尚未绑定已核验企业，未读取舆情。")

    enterprise = _intake_enterprise_validity(enterprise_state or {})
    try:
        part_query = {"supplier_code": supplier_code} if supplier_code else {"supplier_name": company_name}
        part_count = int(db["internal_supplier_material_relations"].count_documents(part_query))
        part_failed = False
    except Exception:
        part_count = 0
        part_failed = True
    history = (
        _state("internal_supplier_material_relations", "failed", "PART_HISTORY_READ_FAILED", "历史零件关系来源读取失败。")
        if part_failed
        else _state(
            "internal_supplier_material_relations",
            "available" if part_count else "missing",
            "HISTORICAL_RELATION_FOUND" if part_count else "HISTORICAL_RELATION_MISSING",
            f"存在 {part_count} 条历史零件合作关系，不代表当前供货能力。" if part_count else "没有历史零件合作关系记录。",
            facts={"count": part_count},
        )
    )

    financial = _state("financial_cache", "missing", "FINANCIAL_SOURCE_MISSING", "当前没有可核验的公开财务来源。")
    try:
        from app.domains.risk.repo_financial import get_financial_metrics

        metric = get_financial_metrics(company_name)
    except Exception:
        metric = None
        financial = _state("financial_cache", "failed", "FINANCIAL_READ_FAILED", "公开财务来源读取失败。")
    if metric is not None and financial["status"] != "failed":
        financial = _state("financial_cache", "unknown", "FINANCIAL_REPORT_PERIOD_MISSING", "已有财务指标，但报告期间和来源观察时间未核验。")

    dimensions = [
        _dimension("enterprise", "企业工商与风险", enterprise),
        _dimension("transaction", "内部月度交易", transaction),
        _dimension("history", "历史零件合作", history),
        _dimension("financial", "公开财务", financial),
        _dimension("sentiment", "舆情分析", sentiment),
    ]
    findings: list[dict] = []
    if transaction.get("facts", {}).get("snapshot_month"):
        findings.append(
            {
                "title": "内部交易快照待核验",
                "evidence": f"最新月份 {transaction['facts']['snapshot_month']}；当前有效性：{transaction['detail']}",
                "status": "partial",
            }
        )
    if part_count:
        findings.append(
            {
                "title": "存在历史零件合作记录",
                "evidence": f"历史关系库中有 {part_count} 条记录；这不表示当前供货或资质已确认。",
                "status": "supported",
            }
        )
    if metric is not None:
        findings.append(
            {
                "title": "已取得公开财务指标",
                "evidence": "财务指标可供后续核验；报告期间和来源有效性仍待确认。",
                "status": "partial",
            }
        )
    if not findings:
        findings.append(
            {
                "title": "尚未形成风险结论",
                "evidence": "当前资料缺少已核验的主体或来源覆盖，不能据此判断低风险。",
                "status": "partial",
            }
        )
    evidence = [
        {"source": "内部交易", "count": transaction.get("facts", {}).get("row_count"), "validity": transaction},
        {"source": "历史零件关系", "count": part_count, "validity": history},
        {"source": "公开财务", "available": financial.get("status") == "available", "validity": financial},
    ]
    return dimensions, findings, evidence
