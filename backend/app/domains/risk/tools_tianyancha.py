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
) -> tuple[dict[str, dict[str, Any]], str, str]:
    """Read cache first, then make one bounded provider call when configured."""
    documents = _cached_documents(company_name, collections)
    if documents:
        return documents, "cached", "已使用本地天眼查快照"
    if not settings.TIANYANCHA_TOKEN:
        return {}, "unavailable", "未配置天眼查访问凭据"

    try:
        from app.services import tianyancha_client

        if fetch_news:
            tianyancha_client.fetch_news(company_name)
        else:
            tianyancha_client.fetch_company(company_name)
    except Exception as exc:
        return {}, "failed", f"天眼查查询失败：{type(exc).__name__}"
    documents = _cached_documents(company_name, collections)
    if documents:
        return documents, "live", "已完成天眼查检索并写入本地快照"
    return {}, "not_found", "天眼查未返回可用资料"


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
        "status": "available" if records else status,
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
    documents, source_mode, source_message = _ensure_documents(name, ("baseinfo",))
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
    )
    candidate = {
        "external_id": f"tyc:{profile.get('id')}" if profile.get("id") else None,
        "legal_name": _text(profile.get("name")) or name,
        "unified_social_credit_code": credit_code or None,
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
            statement=f"天眼查返回主体：{candidate['legal_name']}，统一社会信用代码：{credit_code or '未提供'}",
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
    documents, source_mode, source_message = _ensure_documents(name, collections)
    records_by_type = {collection: _records(documents.get(collection)) for collection in collections}
    counts = {collection: len(records) for collection, records in records_by_type.items()}
    records = [
        {"data_type": collection, "count": count, "items": records_by_type[collection][:10]}
        for collection, count in counts.items()
        if count or collection in documents
    ]
    available = bool(documents)
    payload: dict[str, Any] = {
        "status": "available" if available else source_mode,
        "company_name": name,
        "domain": dimension,
        "source": f"天眼查{label}",
        "source_mode": source_mode,
        "source_message": source_message,
        "queried_at": _now(),
        "counts": counts,
        "records": records,
        "limitations": [] if available else [f"未取得天眼查{label}资料，不能形成该维度结论"],
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
        payload["claims"] = [_claim(
            tool_name=tool_name,
            company_name=name,
            dimension=dimension,
            statement=f"天眼查{label}返回 {sum(counts.values())} 条分类记录",
            value=counts,
        )]
    return _with_evidence(payload, tool_name=tool_name, company_name=name, dimension=dimension)


@tool
def lookup_legal_risk(company_name: str) -> dict:
    """查询诉讼、立案、被执行、失信和限制消费。只读。"""
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
    documents, source_mode, source_message = _ensure_documents(name, ("news",), fetch_news=True)
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
    documents, source_mode, source_message = _ensure_documents(name, _PROFILE_COLLECTIONS)
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
