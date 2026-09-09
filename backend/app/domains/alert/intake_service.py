"""Investigation-first intake for supplier monitoring.

An intake is deliberately separate from a monitor target.  It lets the system
collect evidence and lets a buyer confirm the intended legal entity before a
write creates a durable monitoring object.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.core.config import settings
from app.db.mongo import get_db


INTAKE_COLLECTION = "monitor_intakes"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: object) -> str:
    return str(value or "").strip()


def _baseinfo_result(document: dict | None) -> dict:
    if not isinstance(document, dict):
        return {}
    items = document.get("items")
    if isinstance(items, dict) and isinstance(items.get("result"), dict):
        return dict(items["result"])
    result = document.get("result")
    return dict(result) if isinstance(result, dict) else {}


def _company_candidate(company: dict) -> dict:
    company_id = _text(company.get("company_id") or company.get("id"))
    return {
        "candidate_id": f"company:{company_id}",
        "candidate_type": "company",
        "company_id": company_id,
        "supplier_id": None,
        "supplier_code": None,
        "legal_name": _text(company.get("legal_name")),
        "unified_social_credit_code": company.get("unified_social_credit_code"),
        "registration_status": company.get("registration_status"),
        "verification_status": _text(company.get("verification_status")) or "pending_verification",
        "match_type": _text(company.get("match_type")) or "legal_name",
        "confidence": float(company.get("confidence") or 0),
        "source": "本地主体库",
    }


def _supplier_candidate(supplier: dict, query: str) -> dict:
    supplier_id = _text(supplier.get("supplier_id") or supplier.get("_id"))
    name = _text(supplier.get("name"))
    exact = name == query
    return {
        "candidate_id": f"supplier:{supplier_id}",
        "candidate_type": "supplier",
        "company_id": _text(supplier.get("company_id")) or None,
        "supplier_id": supplier_id,
        "supplier_code": _text(supplier.get("supplier_code")) or None,
        "legal_name": name,
        "unified_social_credit_code": supplier.get("unified_code"),
        "registration_status": supplier.get("reg_status"),
        "verification_status": "verified" if supplier_id else "pending_verification",
        "match_type": "supplier_code" if query == _text(supplier.get("supplier_code")) else "legal_name" if exact else "name_contains",
        "confidence": 1.0 if exact or query == _text(supplier.get("supplier_code")) else 0.82,
        "source": "飞书正式供应商主数据" if supplier.get("source") == "feishu_bitable" else "内部供应商库",
    }


def _load_local_candidates(query: str) -> list[dict]:
    db = get_db()
    candidates: list[dict] = []
    try:
        from app.domains.company.service import search_identity

        identity = search_identity(query, limit=10)
        exact = identity.get("exact")
        rows = [exact] if isinstance(exact, dict) else list(identity.get("candidates") or [])
        candidates.extend(_company_candidate(row) for row in rows if isinstance(row, dict))
    except Exception:
        # A database-backed company lookup must not prevent internal supplier
        # data from being investigated.
        pass

    escaped = re.escape(query)
    from app.domains.sourcing.supplier_repo import has_current_feishu_supplier_snapshot

    use_feishu_snapshot = has_current_feishu_supplier_snapshot(db)
    supplier_collection = db["supplier_master_snapshots"] if use_feishu_snapshot else db["suppliers"]
    supplier_filter: dict[str, Any] = {
        "$or": [
            {"name": query},
            {"supplier_code": query},
            {"name": {"$regex": escaped, "$options": "i"}},
        ],
    }
    if use_feishu_snapshot:
        supplier_filter.update({"source": "feishu_bitable", "sync_status": "current"})
    supplier_rows = list(supplier_collection.find(supplier_filter).limit(10))
    candidates.extend(_supplier_candidate(row, query) for row in supplier_rows)

    unique: dict[str, dict] = {}
    for candidate in candidates:
        identifier = _text(candidate.get("company_id")) or _text(candidate.get("supplier_id"))
        if identifier and identifier not in unique:
            unique[identifier] = candidate
    return sorted(unique.values(), key=lambda item: (-float(item["confidence"]), item["legal_name"]))


def _load_external_profile(query: str) -> tuple[dict | None, dict]:
    """Read cached Tianyancha evidence, fetching only for an explicit intake."""
    db = get_db()
    cached = db["baseinfo"].find_one({"name": query})
    source_state = {"key": "enterprise", "label": "企业工商与风险", "status": "available" if cached else "not_queried", "detail": "已使用本地天眼查快照" if cached else ""}
    if not cached and settings.TIANYANCHA_TOKEN:
        try:
            from app.services.tianyancha_client import fetch_company

            fetch_company(query)
            cached = db["baseinfo"].find_one({"name": query})
            source_state = {"key": "enterprise", "label": "企业工商与风险", "status": "available" if cached else "failed", "detail": "已完成天眼查检索" if cached else "天眼查未返回可用主体资料"}
        except Exception:
            source_state = {"key": "enterprise", "label": "企业工商与风险", "status": "failed", "detail": "天眼查查询失败，可稍后重试"}
    elif not cached:
        source_state = {"key": "enterprise", "label": "企业工商与风险", "status": "unavailable", "detail": "未配置天眼查访问凭据"}

    profile = _baseinfo_result(cached)
    if not profile:
        return None, source_state
    return {
        "company_name": _text(profile.get("name")) or query,
        "unified_social_credit_code": profile.get("regNumber"),
        "registration_status": profile.get("regStatus"),
        "legal_person": profile.get("legalPersonName"),
        "industry": profile.get("industry"),
        "source_reference": f"tyc:{_text(profile.get('name')) or query}",
    }, source_state


def _candidate_context(candidate: dict | None, query: str) -> tuple[str, str | None, str | None]:
    if not candidate:
        return query, None, None
    return (
        _text(candidate.get("legal_name")) or query,
        candidate.get("supplier_id"),
        candidate.get("supplier_code"),
    )


def _data_coverage(candidate: dict | None, query: str, enterprise_state: dict) -> tuple[list[dict], list[dict], list[dict]]:
    db = get_db()
    company_name, supplier_id, supplier_code = _candidate_context(candidate, query)
    transaction_query: dict[str, Any] = {"sync_status": "current"}
    if supplier_code:
        transaction_query["supplier_code"] = supplier_code
    elif supplier_id:
        transaction_query["supplier_id"] = supplier_id
    else:
        transaction_query = {"supplier_name": company_name, "sync_status": "current"}
    transactions = list(db["supplier_transaction_snapshots"].find(transaction_query).sort("snapshot_month", -1).limit(24))
    part_query: dict[str, Any] = {"supplier_code": supplier_code} if supplier_code else {"supplier_name": company_name}
    part_count = db["internal_supplier_material_relations"].count_documents(part_query)
    sentiment = db["sentiment_results"].find_one({"company_name": company_name})

    financial = None
    try:
        from app.domains.risk.repo_financial import get_financial_metrics

        metric = get_financial_metrics(company_name)
        financial = metric.model_dump() if metric else None
    except Exception:
        financial = None

    dimensions = [
        {"key": "enterprise", "label": "企业工商与风险", "status": enterprise_state["status"], "detail": enterprise_state["detail"] or "尚未取得企业资料"},
        {"key": "transaction", "label": "内部月度交易", "status": "available" if transactions else "missing", "detail": f"已关联 {len(transactions)} 条月度快照" if transactions else "未关联内部交易快照"},
        {"key": "history", "label": "历史零件合作", "status": "available" if part_count else "missing", "detail": f"已关联 {part_count} 条零件—供应商关系" if part_count else "未关联历史零件关系"},
        {"key": "financial", "label": "公开财务", "status": "available" if financial else "missing", "detail": "已取得公开财务指标" if financial else "未取得可用财务指标"},
        {"key": "sentiment", "label": "舆情分析", "status": "available" if sentiment else "missing", "detail": "已有舆情分析结果" if sentiment else "尚未形成舆情分析"},
    ]
    findings: list[dict] = []
    if transactions:
        latest = transactions[0]
        findings.append({"title": "已关联内部交易", "evidence": f"最新月度快照：{latest.get('snapshot_month') or '期间未知'}；收货记录数 {latest.get('receipt_record_count') or latest.get('receipt_count') or '未提供'}。", "status": "supported"})
    if part_count:
        findings.append({"title": "存在历史零件合作记录", "evidence": f"内部历史关系库中有 {part_count} 条零件—供应商关系。", "status": "supported"})
    if financial:
        findings.append({"title": "已取得公开财务指标", "evidence": "可在首次风险评估中使用已取得的财务指标；需以报告期间和来源为准。", "status": "supported"})
    if not findings:
        findings.append({"title": "尚未形成风险结论", "evidence": "当前仅完成线索调查，资料不足不能被解释为低风险。", "status": "partial"})
    return dimensions, findings, [{"source": "内部交易", "count": len(transactions)}, {"source": "历史零件关系", "count": part_count}, {"source": "公开财务", "available": bool(financial)}]


def _serialize(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def investigate_supplier_monitoring(query: str) -> dict:
    """Return the same evidence-backed investigation used by monitoring intake.

    This is read-only so the Harness can explain findings without silently
    creating a monitor target or a user-owned intake.
    """
    normalized_query = _text(query)
    if len(normalized_query) < 2:
        raise ValueError("请输入至少两个字符的企业名称、供应商代码或统一社会信用代码")
    candidates = _load_local_candidates(normalized_query)
    selected = candidates[0] if len(candidates) == 1 else None
    external_profile, enterprise_state = _load_external_profile(normalized_query)
    dimensions, findings, evidence = _data_coverage(selected, normalized_query, enterprise_state)
    return _serialize({
        "query": normalized_query,
        "status": "ready_for_selection" if candidates else "needs_identity_confirmation",
        "candidates": candidates,
        "selected_candidate_id": selected.get("candidate_id") if selected else None,
        "external_profile": external_profile,
        "data_coverage": {"dimensions": dimensions, "missing_dimensions": [item["label"] for item in dimensions if item["status"] != "available"]},
        "findings": findings,
        "evidence_summary": evidence,
    })


def create_monitor_intake(query: str, user_id: str) -> dict:
    investigation = investigate_supplier_monitoring(query)
    now = _now()
    document = {
        "intake_id": str(uuid.uuid4()),
        **investigation,
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
    }
    get_db()[INTAKE_COLLECTION].insert_one(document)
    return _serialize(document)


def get_monitor_intake(intake_id: str, user_id: str) -> dict | None:
    document = get_db()[INTAKE_COLLECTION].find_one({"intake_id": intake_id, "created_by": user_id})
    return _serialize(document) if isinstance(document, dict) else None


def select_monitor_intake_candidate(intake_id: str, candidate_id: str, user_id: str) -> dict:
    document = get_db()[INTAKE_COLLECTION].find_one({"intake_id": intake_id, "created_by": user_id})
    if not isinstance(document, dict):
        raise ValueError("调查不存在或无权访问")
    candidate = next((item for item in document.get("candidates", []) if item.get("candidate_id") == candidate_id), None)
    if not isinstance(candidate, dict):
        raise ValueError("请选择本次调查返回的主体候选")
    dimensions, findings, evidence = _data_coverage(candidate, _text(document.get("query")), {"key": "enterprise", "label": "企业工商与风险", "status": "available" if document.get("external_profile") else "missing", "detail": "已取得企业资料" if document.get("external_profile") else "尚未取得企业资料"})
    get_db()[INTAKE_COLLECTION].update_one({"_id": document["_id"]}, {"$set": {"selected_candidate_id": candidate_id, "status": "ready_for_confirmation", "data_coverage": {"dimensions": dimensions, "missing_dimensions": [item["label"] for item in dimensions if item["status"] != "available"]}, "findings": findings, "evidence_summary": evidence, "updated_at": _now()}})
    return get_monitor_intake(intake_id, user_id) or {}


def confirm_monitor_intake(intake_id: str, user_id: str) -> dict:
    document = get_db()[INTAKE_COLLECTION].find_one({"intake_id": intake_id, "created_by": user_id})
    if not isinstance(document, dict):
        raise ValueError("调查不存在或无权访问")
    if document.get("monitor_target_id"):
        return {"status": "already_confirmed", "monitor_target_id": document["monitor_target_id"]}
    selected_id = _text(document.get("selected_candidate_id"))
    candidate = next((item for item in document.get("candidates", []) if item.get("candidate_id") == selected_id), None)
    if not isinstance(candidate, dict):
        raise ValueError("请先选择一个主体候选")

    from app.domains.alert.service import add_to_watchlist, get_watchlist_target_summaries, save_snapshot
    from app.domains.risk.service import calculate_company_risk_preview

    target = add_to_watchlist(
        candidate.get("legal_name"),
        target_type="formal_supplier" if candidate.get("supplier_id") else "company",
        supplier_id=candidate.get("supplier_id"),
        company_id=candidate.get("company_id"),
        supplier_code=candidate.get("supplier_code"),
    )
    baseline_status = "not_available"
    try:
        preview = calculate_company_risk_preview(target["company_name"])
        if preview is not None:
            save_snapshot(target["company_name"], preview, monitor_target_id=target["monitor_target_id"], target_type=target.get("target_type"), supplier_id=target.get("supplier_id"), company_id=target.get("company_id"))
            baseline_status = "created"
    except Exception:
        baseline_status = "partial"
    summary = next((item for item in get_watchlist_target_summaries() if item.get("monitor_target_id") == target["monitor_target_id"]), target)
    get_db()[INTAKE_COLLECTION].update_one({"_id": document["_id"]}, {"$set": {"status": "confirmed", "monitor_target_id": target["monitor_target_id"], "baseline_status": baseline_status, "confirmed_at": _now(), "updated_at": _now()}})
    return _serialize({"status": "confirmed", "monitor_target": summary, "baseline_status": baseline_status})
