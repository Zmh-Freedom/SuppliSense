"""受控的天眼查业务查询工具。

工具只暴露采购业务能力，不允许 Agent 直接传入任意天眼查 endpoint。
所有结果都保留来源、缓存/实时状态和证据边界；工具本身只读。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool

from app.core.config import settings
from app.db.mongo import get_db


_LEGAL_COLLECTIONS = (
    "lawSuit",
    "courtRegister",
    "dishonesty",
    "executedPerson",
    "courtAnnouncement",
    "consumptionRestriction",
)
_BUSINESS_COLLECTIONS = (
    "riskInfo",
    "abnormal",
    "punishmentInfo",
    "illegalinfo",
    "equityPledge",
    "taxArrears",
)
_PROFILE_COLLECTIONS = ("baseinfo", "holder", "invest", "changeInfo", "branch")

_COLLECTION_LABELS = {
    "lawSuit": "法律诉讼",
    "courtRegister": "立案信息",
    "dishonesty": "失信被执行人",
    "executedPerson": "被执行人",
    "courtAnnouncement": "开庭公告",
    "consumptionRestriction": "限制消费",
    "riskInfo": "综合风险信息",
    "abnormal": "经营异常",
    "punishmentInfo": "行政处罚",
    "illegalinfo": "严重违法",
    "equityPledge": "股权质押",
    "taxArrears": "欠税公告",
}
_STATUS_LABELS = {
    "has_records": "有记录",
    "no_records": "明确无记录",
    "not_queried": "未查询",
    "query_failed": "查询失败",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unwrap(document: dict[str, Any] | None) -> Any:
    if not isinstance(document, dict):
        return None
    value: Any = document.get("items")
    if value is None:
        value = document.get("item")
    if isinstance(value, dict) and isinstance(value.get("result"), (dict, list)):
        value = value["result"]
    if value is None and isinstance(document.get("result"), (dict, list)):
        value = document["result"]
    return value


def _records(document: dict[str, Any] | None, limit: int = 20) -> list[dict[str, Any]]:
    value = _unwrap(document)
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        value = value["items"]
    if isinstance(value, list):
        return [item for item in value[:limit] if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _cached_documents(company_name: str, collections: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    try:
        db = get_db()
        return {
            collection: document
            for collection in collections
            if isinstance(document := db[collection].find_one({"name": company_name}), dict)
        }
    except Exception:
        # A cache/database outage must become an explicit unavailable result;
        # it should never turn a read-only Agent query into a tool exception.
        return {}


def _ensure_documents(
    company_name: str,
    collections: tuple[str, ...],
    *,
    fetch_news: bool = False,
) -> tuple[dict[str, dict[str, Any]], str, str, dict[str, str]]:
    """Read cache first, then query only missing provider collections."""
    documents = _cached_documents(company_name, collections)
    missing = [collection for collection in collections if collection not in documents]
    if not missing:
        return documents, "cached", "已使用本地天眼查快照", {collection: "cached" for collection in collections}
    if not settings.TIANYANCHA_TOKEN:
        return documents, "cached" if documents else "unavailable", "未配置天眼查访问凭据", {collection: "not_queried" for collection in missing}

    try:
        from app.services import tianyancha_client

        if fetch_news:
            response = tianyancha_client.fetch_news(company_name)
            fetch_statuses = {"news": "queried" if response is not None else "query_failed"}
        else:
            fetch_statuses = tianyancha_client.fetch_collections(company_name, missing)
    except Exception as exc:
        return documents, "failed" if not documents else "partial", f"天眼查查询失败：{type(exc).__name__}", {collection: "query_failed" for collection in missing}
    documents = _cached_documents(company_name, collections)
    if documents:
        failed = any(fetch_statuses.get(collection) == "query_failed" for collection in missing)
        return documents, "partial" if failed else "live", "已完成天眼查缺失维度检索并写入本地快照", fetch_statuses
    if any(value == "query_failed" for value in fetch_statuses.values()):
        return {}, "failed", "天眼查查询失败", fetch_statuses
    return {}, "not_found", "天眼查未返回可用资料", fetch_statuses


def _unpack_ensure_result(result: Any) -> tuple[dict[str, dict[str, Any]], str, str, dict[str, str]]:
    """Support historical test doubles that return the original three values."""
    if isinstance(result, tuple) and len(result) == 4:
        documents, source_mode, source_message, fetch_statuses = result
        return documents, source_mode, source_message, dict(fetch_statuses or {})
    documents, source_mode, source_message = result
    return documents, source_mode, source_message, {}


def _collection_status(document: dict[str, Any] | None, attempted: str | None) -> str:
    if document is not None:
        return "has_records" if _records(document) or _provider_total(document) > 0 else "no_records"
    if attempted in {"query_failed", "queried"}:
        return "query_failed"
    return "not_queried"


def _provider_total(document: dict[str, Any] | None) -> int:
    value = _unwrap(document)
    if isinstance(value, dict):
        for candidate in (value.get("total"), (value.get("result") or {}).get("total") if isinstance(value.get("result"), dict) else None):
            if isinstance(candidate, (int, float)):
                return int(candidate)
    return 0


def _collection_statuses(
    collections: tuple[str, ...],
    documents: dict[str, dict[str, Any]],
    fetch_statuses: dict[str, str],
) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for collection in collections:
        status = _collection_status(documents.get(collection), fetch_statuses.get(collection))
        statuses[collection] = {
            "label": _COLLECTION_LABELS.get(collection, collection),
            "status": status,
            "status_label": _STATUS_LABELS[status],
            "count": (_provider_total(documents.get(collection)) or len(_records(documents.get(collection)))) if documents.get(collection) else 0,
            "source_mode": "cached" if collection in documents and fetch_statuses.get(collection) != "queried" else ("live" if fetch_statuses.get(collection) == "queried" else "none"),
        }
    return statuses


def _base_profile(document: dict[str, Any] | None) -> dict[str, Any]:
    value = _unwrap(document)
    return dict(value) if isinstance(value, dict) else {}


def _provider_evidence(
    *,
    tool_name: str,
    company_name: str,
    dimension: str,
    status: str,
    source_mode: str,
    source_message: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [{
        "evidence_id": f"{tool_name}:tianyancha:{company_name}:{dimension}",
        "entity_id": f"entity:{company_name}",
        "dimension": dimension,
        "provider": "tianyancha",
        "source_type": "tianyancha_api",
        "status": "available" if any(item.get("status") in {"has_records", "no_records"} for item in records) else status,
        "collected_at": _now(),
        "data_mode": "formal",
        "facts": {
            "company_name": company_name,
            "source_mode": source_mode,
            "source_message": source_message,
            "records": records,
        },
    }]


def _claim(
    *,
    tool_name: str,
    company_name: str,
    dimension: str,
    statement: str,
    value: Any,
) -> dict[str, Any]:
    return {
        "claim_id": f"{tool_name}:tianyancha:{company_name}:{dimension}",
        "entity_id": f"entity:{company_name}",
        "dimension": dimension,
        "statement": statement,
        "value": value,
        "operator": "observed",
        "evidence_refs": [f"{tool_name}:tianyancha:{company_name}:{dimension}"],
        "confidence": 0.95,
    }


def _with_evidence(
    payload: dict[str, Any],
    *,
    tool_name: str,
    company_name: str,
    dimension: str,
) -> dict[str, Any]:
    from app.tools.evidence import attach_tool_evidence

    result = attach_tool_evidence(
        payload,
        tool_name=tool_name,
        entity_id=f"entity:{company_name}",
        dimension=dimension,
        source_type="tianyancha_api",
    )
    # ``evidence`` is the legacy provider field used only as input to the
    # shared normalizer.  The strict registry contracts expose the normalized
    # ``evidence_records`` field instead, so do not leak the legacy key into
    # the validated tool response.
    result.pop("evidence", None)
    return result


@tool
def lookup_company_identity(company_name: str, unified_social_credit_code: str = "") -> dict:
    """查询企业主体、统一社会信用代码、法人和登记状态。只读。"""
    name = _text(company_name)
    if len(name) < 2:
        return {
            "status": "invalid",
            "company_name": name,
            "resolution": "invalid",
            "candidate": None,
            "candidates": [],
            "message": "企业名称至少需要两个字符",
        }
    documents, source_mode, source_message, _ = _unpack_ensure_result(_ensure_documents(name, ("baseinfo",)))
    profile = _base_profile(documents.get("baseinfo"))
    if not profile:
        return {
            "status": source_mode,
            "company_name": name,
            "resolution": "not_found",
            "candidate": None,
            "candidates": [],
            "source": "天眼查工商主体查询",
            "source_mode": source_mode,
            "source_message": source_message,
            "queried_at": _now(),
            "limitations": ["本轮未取得可用工商主体资料，不能形成主体结论"],
        }
    credit_code = _text(
        profile.get("creditCode")
        or profile.get("taxNumber")
        or profile.get("unifiedSocialCreditCode")
    ) or None
    registration_number = _text(
        profile.get("regNumber") or profile.get("registrationNumber")
    ) or None
    candidate = {
        "external_id": f"tyc:{profile.get('id')}" if profile.get("id") else None,
        "legal_name": _text(profile.get("name")) or name,
        "unified_social_credit_code": credit_code,
        "registration_number": registration_number,
        "legal_person": _text(profile.get("legalPersonName")),
        "registration_status": _text(profile.get("regStatus")),
        "registered_address": _text(profile.get("regLocation")),
        "source": "天眼查工商主体查询",
        "source_reference": f"tyc:{credit_code or profile.get('id') or name}",
        "verification_status": "matched" if not unified_social_credit_code or credit_code == _text(unified_social_credit_code) else "conflict",
    }
    conflict = candidate["verification_status"] == "conflict"
    payload = {
        "status": "conflict" if conflict else "available",
        "company_name": name,
        "resolution": "candidates" if conflict else "exact",
        "candidate": candidate,
        "candidates": [candidate],
        "source": "天眼查工商主体查询",
        "source_mode": source_mode,
        "source_message": source_message,
        "queried_at": _now(),
        "limitations": ["天眼查候选不能替代监控对象主体绑定，仍需用户确认"]
        + (["本轮仅返回注册号，尚未取得统一社会信用代码"] if not credit_code and registration_number else [])
        + (["统一社会信用代码与用户提供值不一致"] if conflict else []),
        "evidence": _provider_evidence(
            tool_name="lookup_company_identity",
            company_name=name,
            dimension="identity_review",
            status=source_mode,
            source_mode=source_mode,
            source_message=source_message,
            records=[candidate],
        ),
        "claims": [_claim(
            tool_name="lookup_company_identity",
            company_name=name,
            dimension="identity_review",
            statement=f"天眼查返回主体：{candidate['legal_name']}，统一社会信用代码：{credit_code or '未提供'}，注册号：{registration_number or '未提供'}",
            value=candidate,
        )],
    }
    return _with_evidence(payload, tool_name="lookup_company_identity", company_name=name, dimension="identity_review")


def _lookup_risk_domain(
    company_name: str,
    collections: tuple[str, ...],
    dimension: str,
    tool_name: str,
    label: str,
) -> dict[str, Any]:
    name = _text(company_name)
    if len(name) < 2:
        return {
            "status": "invalid",
            "company_name": name,
            "domain": dimension,
            "counts": {},
            "records": [],
            "limitations": ["企业名称至少需要两个字符"],
            "message": "企业名称至少需要两个字符",
        }
    documents, source_mode, source_message, fetch_statuses = _unpack_ensure_result(_ensure_documents(name, collections))
    records_by_type = {collection: _records(documents.get(collection)) for collection in collections}
    counts = {
        collection: (_provider_total(documents.get(collection)) or len(records))
        for collection, records in records_by_type.items()
    }
    collection_statuses = _collection_statuses(collections, documents, fetch_statuses)
    records = [
        {
            "data_type": collection,
            "label": collection_statuses[collection]["label"],
            "count": count,
            "status": collection_statuses[collection]["status"],
            "status_label": collection_statuses[collection]["status_label"],
            "items": records_by_type[collection][:10],
        }
        for collection, count in counts.items()
    ]
    available = any(item["status"] in {"has_records", "no_records"} for item in collection_statuses.values())
    dimension_limitations = [
        f"{item['label']}：{item['status_label']}"
        for item in collection_statuses.values()
        if item["status"] in {"not_queried", "query_failed"}
    ]
    limitations = ([] if available else [f"未取得天眼查{label}资料，不能形成该维度结论"]) + dimension_limitations
    payload: dict[str, Any] = {
        "status": "available" if available else source_mode,
        "company_name": name,
        "domain": dimension,
        "source": f"天眼查{label}",
        "source_mode": source_mode,
        "source_message": source_message,
        "queried_at": _now(),
        "counts": counts,
        "collection_statuses": collection_statuses,
        "records": records,
        "limitations": limitations,
        "evidence": _provider_evidence(
            tool_name=tool_name,
            company_name=name,
            dimension=dimension,
            status=source_mode,
            source_mode=source_mode,
            source_message=source_message,
            records=records,
        ),
    }
    if available:
        payload["claims"] = [
            _claim(
                tool_name=tool_name,
                company_name=name,
                dimension=dimension,
                statement=f"天眼查{_COLLECTION_LABELS.get(collection, collection)}：{collection_statuses[collection]['status_label']}（{counts[collection]} 条）",
                value=counts[collection],
            )
            for collection in collections
            if collection_statuses[collection]["status"] in {"has_records", "no_records"}
        ]
    return _with_evidence(payload, tool_name=tool_name, company_name=name, dimension=dimension)


@tool
def lookup_legal_risk(company_name: str) -> dict:
    """查询诉讼、立案、被执行、失信、开庭公告和限制消费。只读。"""
    return _lookup_risk_domain(company_name, _LEGAL_COLLECTIONS, "legal_risk", "lookup_legal_risk", "司法风险")


@tool
def lookup_business_risk(company_name: str) -> dict:
    """查询行政处罚、经营异常、严重违法、股权质押和欠税。只读。"""
    return _lookup_risk_domain(company_name, _BUSINESS_COLLECTIONS, "business_risk", "lookup_business_risk", "经营风险")


@tool
def lookup_company_news(company_name: str) -> dict:
    """查询企业新闻和舆情。只读。"""
    name = _text(company_name)
    if len(name) < 2:
        return {
            "status": "invalid",
            "company_name": name,
            "articles_count": 0,
            "articles": [],
            "limitations": ["企业名称至少需要两个字符"],
            "message": "企业名称至少需要两个字符",
        }
    documents, source_mode, source_message, _ = _unpack_ensure_result(_ensure_documents(name, ("news",), fetch_news=True))
    records = _records(documents.get("news"), limit=30)
    payload = {
        "status": "available" if documents else source_mode,
        "company_name": name,
        "source": "天眼查企业新闻",
        "source_mode": source_mode,
        "source_message": source_message,
        "queried_at": _now(),
        "articles_count": len(records),
        "articles": records,
        "limitations": [] if documents else ["未取得天眼查新闻资料，不能形成舆情结论"],
        "evidence": _provider_evidence(
            tool_name="lookup_company_news",
            company_name=name,
            dimension="sentiment",
            status=source_mode,
            source_mode=source_mode,
            source_message=source_message,
            records=records,
        ),
    }
    if documents:
        payload["claims"] = [_claim(
            tool_name="lookup_company_news",
            company_name=name,
            dimension="sentiment",
            statement=f"天眼查新闻快照包含 {len(records)} 条记录",
            value=len(records),
        )]
    return _with_evidence(payload, tool_name="lookup_company_news", company_name=name, dimension="sentiment")


@tool
def lookup_company_profile(company_name: str) -> dict:
    """查询注册资本、注册地址、历史变更、股东和分支机构。只读。"""
    name = _text(company_name)
    if len(name) < 2:
        return {
            "status": "invalid",
            "company_name": name,
            "profile": {},
            "limitations": ["企业名称至少需要两个字符"],
            "message": "企业名称至少需要两个字符",
        }
    documents, source_mode, source_message, _ = _unpack_ensure_result(_ensure_documents(name, _PROFILE_COLLECTIONS))
    profile = _base_profile(documents.get("baseinfo"))
    sections = {
        "baseinfo": profile,
        "holder": _records(documents.get("holder")),
        "invest": _records(documents.get("invest")),
        "changeInfo": _records(documents.get("changeInfo")),
        "branch": _records(documents.get("branch")),
    }
    available = bool(documents)
    payload = {
        "status": "available" if available else source_mode,
        "company_name": name,
        "source": "天眼查工商资料",
        "source_mode": source_mode,
        "source_message": source_message,
        "queried_at": _now(),
        "profile": sections,
        "limitations": [] if available else ["未取得天眼查工商资料，不能形成工商信息结论"],
        "evidence": _provider_evidence(
            tool_name="lookup_company_profile",
            company_name=name,
            dimension="company_profile",
            status=source_mode,
            source_mode=source_mode,
            source_message=source_message,
            records=[{"section": key, "count": len(value) if isinstance(value, list) else int(bool(value))} for key, value in sections.items()],
        ),
    }
    if available:
        payload["claims"] = [_claim(
            tool_name="lookup_company_profile",
            company_name=name,
            dimension="company_profile",
            statement=f"天眼查工商资料已覆盖 {len(documents)} 个资料域",
            value={key: len(value) if isinstance(value, list) else int(bool(value)) for key, value in sections.items()},
        )]
    return _with_evidence(payload, tool_name="lookup_company_profile", company_name=name, dimension="company_profile")


__all__ = [
    "lookup_company_identity",
    "lookup_legal_risk",
    "lookup_business_risk",
    "lookup_company_news",
    "lookup_company_profile",
]
