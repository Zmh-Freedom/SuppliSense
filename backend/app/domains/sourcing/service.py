"""
Sourcing service — 智能寻源业务逻辑。

框架无关，service 层不引入 LangGraph 依赖。
风险评估改用 MongoDB alert_snapshots 快速查分，不再调外部 API。
"""

import uuid
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.db.postgres import get_cursor
from app.domains.sourcing.repo import (
    approve_access_application,
    create_access_application,
    create_request,
    get_access_application,
    get_access_application_by_candidate,
    get_external_candidate,
    get_external_candidate_by_name,
    get_request,
    get_result,
    get_results,
    list_access_applications,
    reject_access_application,
    save_result,
    save_external_candidate,
    update_request_status,
    update_result_action,
)
from app.schemas.sourcing import SourcingRequestInput
from app.domains.knowledge.embedding import encode_single

logger = get_logger(__name__)

# 向量相似度只能负责召回，不能替代采购品类约束。
_CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "钢材": (
        "钢材", "钢板", "钢卷", "型钢", "不锈钢", "合金钢", "碳钢", "钢管",
        "钢筋", "线材", "棒材",
    ),
}


def create_sourcing_request(req: SourcingRequestInput, user_id: str) -> str:
    rid = create_request({
        "user_id": user_id,
        "title": req.title,
        "category": req.category,
        "spec": req.spec,
        "budget_min": req.budget_min,
        "budget_max": req.budget_max,
        "quantity": req.quantity,
        "region_required": req.region_required,
        "qualifications": req.qualifications,
    })
    logger.info("sourcing_request_created", request_id=rid, title=req.title)
    return rid


def search_suppliers(request_id: str) -> dict[str, Any]:
    """
    核心寻源流程：
    1. 读取需求 → 2. 向量检索 Top-20 → 3. 并发风险评估 → 4. 加权排序 → 5. 持久化结果
    """
    req_doc = get_request(request_id)
    if not req_doc:
        raise ValueError(f"寻源请求不存在: {request_id}")

    update_request_status(request_id, "searching")

    # 1. 构建查询文本（品类 + 规格 + 地域）
    query_parts = [req_doc.get("category", "")]
    query_parts.append(req_doc.get("spec", ""))
    if req_doc.get("region_required"):
        query_parts.append(req_doc["region_required"])
    query_text = " ".join(p for p in query_parts if p)

    # 2. 正式供应商优先使用飞书三表快照；未启用时保持 PG 向量检索兼容路径。
    snapshot_candidates = _search_feishu_snapshot_suppliers(req_doc)
    candidates = snapshot_candidates if snapshot_candidates is not None else _vector_search(query_text, top_k=10)
    candidates = _filter_category_candidates(candidates, req_doc.get("category", ""))
    external_candidates: list[dict] = []
    if len(candidates) < 3:
        from app.domains.sourcing_risk.discovery_service import search_external_provider, stage_external_candidates

        external_candidates = stage_external_candidates(
            request_id,
            search_external_provider({
                "category": req_doc.get("category", ""),
                "specification": req_doc.get("spec", ""),
                "region": req_doc.get("region_required", ""),
            }),
        )
        for candidate in external_candidates:
            save_external_candidate(candidate)
    if not candidates and not external_candidates:
        update_request_status(request_id, "done", 0)
        return {
            "request_id": request_id,
            "status": "done",
            "results": [],
            "external_candidates": [],
            "message": f"本地供应商库未找到品类“{req_doc.get('category', '')}”的匹配结果",
        }

    # 3. 快速风险查分（MongoDB 快照，不调外部 API）
    risk_map = _batch_assess_risk(candidates) if candidates else {}

    # 4. 排序
    results = []
    for i, supp in enumerate(candidates):
        name = supp["supplier_name"]
        match_score = supp["match_score"]
        risk_info = risk_map.get(name, {})
        risk_score = risk_info.get("risk_score", 50)
        risk_level = risk_info.get("risk_level", "unknown")

        # 加权公式
        final_rank = 0.6 * match_score + 0.4 * (1 - risk_score / 100)
        # 匹配分衰减（越靠后匹配越低）
        final_rank *= (1 - i * 0.02)

        rid = str(uuid.uuid4())
        match_reasons = supp.get("match_reasons") or []
        match_reason = "、".join(match_reasons) if match_reasons else risk_info.get("summary", "")
        item = {
            "result_id": rid,
            "candidate_type": "local",
            "supplier_id": supp.get("supplier_id"),
            "supplier_code": supp.get("supplier_code"),
            "supplier_name": name,
            "match_score": round(match_score, 3),
            "risk_score": risk_score,
            "risk_level": risk_level,
            "final_rank": round(final_rank, 3),
            "match_reason": match_reason,
            "risk_summary": risk_info.get("summary", ""),
            "industry": supp.get("industry"),
            "categories": supp.get("categories", []),
            "capabilities": supp.get("capabilities", []),
            "contacts": supp.get("contacts", []),
            "website_url": supp.get("website_url"),
            "contact_person": supp.get("contact_person"),
            "contact_phone": supp.get("contact_phone"),
            "contact_email": supp.get("contact_email"),
            "source": supp.get("source"),
            "source_updated_at": supp.get("source_updated_at"),
            "selected": False,
            "action": None,
        }
        results.append(item)

    # 按 final_rank 降序排列
    results.sort(key=lambda r: r["final_rank"], reverse=True)
    top10 = results[:10]

    # 5. 持久化
    for r in top10:
        save_result(r["result_id"], {
            "request_id": request_id,
            "supplier_id": r["supplier_id"],
            "supplier_name": r["supplier_name"],
            "match_score": r["match_score"],
            "risk_score": r["risk_score"],
            "risk_level": r["risk_level"],
            "final_rank": r["final_rank"],
            "match_reason": r["match_reason"],
            "risk_summary": r["risk_summary"],
            "supplier_code": r["supplier_code"],
            "industry": r["industry"],
            "categories": r["categories"],
            "capabilities": r["capabilities"],
            "contacts": r["contacts"],
            "website_url": r["website_url"],
            "contact_person": r["contact_person"],
            "contact_phone": r["contact_phone"],
            "contact_email": r["contact_email"],
            "source": r["source"],
            "source_updated_at": r["source_updated_at"],
        })

    update_request_status(request_id, "done", len(top10))
    logger.info("sourcing_complete", request_id=request_id, results=len(top10))

    return {
        "request_id": request_id,
        "status": "done",
        "external_candidates": external_candidates,
        "external_status": "staged" if external_candidates else "not_required",
        "message": (
            f"本地暂无“{req_doc.get('category', '')}”匹配，以下为天眼查和联网搜索的待核验候选。"
            if external_candidates and not candidates
            else "已综合本地历史候选，外部补充结果待人工核验。"
            if external_candidates
            else "已返回本地供应商候选。"
        ),
        "results": [{
            "result_id": r["result_id"],
            "candidate_type": r["candidate_type"],
            "supplier_id": r["supplier_id"],
            "supplier_code": r["supplier_code"],
            "supplier_name": r["supplier_name"],
            "match_score": r["match_score"],
            "risk_score": r["risk_score"],
            "risk_level": r["risk_level"],
            "final_rank": r["final_rank"],
            "match_reason": r["match_reason"],
            "risk_summary": r["risk_summary"],
            "industry": r["industry"],
            "categories": r["categories"],
            "capabilities": r["capabilities"],
            "contacts": r["contacts"],
            "website_url": r["website_url"],
            "contact_person": r["contact_person"],
            "contact_phone": r["contact_phone"],
            "contact_email": r["contact_email"],
            "source": r["source"],
            "source_updated_at": r["source_updated_at"],
        } for r in top10],
    }


def select_result(result_id: str, action: str, user_id: str) -> dict:
    result = get_result(result_id)
    if not result:
        raise ValueError(f"寻源结果不存在: {result_id}")

    supplier_name = result.get("supplier_name", "")
    request_id = result.get("request_id")

    update_result_action(result_id, action)

    if action == "watchlist":
        from app.domains.alert.service import add_to_watchlist
        add_to_watchlist(supplier_name)
        return {"success": True, "action": "watchlist", "message": f"已将 {supplier_name} 加入监控列表"}
    elif action == "apply_access":
        aid = create_access_application(
            supplier_name=supplier_name,
            request_id=request_id,
            applicant_id=user_id,
        )
        return {"success": True, "action": "apply_access", "application_id": aid}

    return {"success": False, "message": f"未知动作: {action}"}


def select_external_candidate(
    candidate_id: str = "",
    action: str = "apply_access",
    user_id: str = "agent",
    supplier_name: str = "",
) -> dict:
    """Execute an approved action for a staged external candidate."""
    candidate = get_external_candidate(candidate_id) if candidate_id else None
    if not candidate and supplier_name.strip():
        candidate = get_external_candidate_by_name(supplier_name)
        candidate_id = str(candidate.get("_id", "")) if candidate else ""
    if not candidate:
        raise ValueError(f"外部候选不存在或已过期: {supplier_name or candidate_id}")
    if candidate.get("status") not in {"staged_candidate", "access_pending"}:
        raise ValueError(f"外部候选当前状态不可执行: {candidate.get('status', 'unknown')}")
    if action != "apply_access":
        raise ValueError(f"外部候选暂不支持动作: {action}")
    if candidate.get("identity_status") != "exact":
        raise ValueError("外部候选尚未完成天眼查唯一身份核验，不能申请准入")

    existing = get_access_application_by_candidate(candidate_id)
    if existing:
        return {
            "success": True,
            "action": action,
            "application_id": str(existing["_id"]),
            "candidate_id": candidate_id,
            "message": "该外部候选已有准入申请，未重复创建",
        }

    aid = create_access_application(
        supplier_name=candidate.get("tianyancha_company_name") or candidate.get("supplier_name", ""),
        request_id=candidate.get("request_id"),
        applicant_id=user_id,
        candidate_id=candidate_id,
    )
    from app.db.mongo import get_db
    get_db()["external_supplier_candidates"].update_one(
        {"_id": candidate_id}, {"$set": {"status": "access_pending", "access_application_id": aid}}
    )
    return {"success": True, "action": action, "application_id": aid, "candidate_id": candidate_id}


def get_request_detail(request_id: str) -> dict | None:
    req = get_request(request_id)
    if not req:
        return None
    results = get_results(request_id)
    return {
        "request_id": str(req.get("_id", req.get("request_id", ""))),
        "user_id": req.get("user_id", ""),
        "title": req.get("title", ""),
        "category": req.get("category", ""),
        "spec": req.get("spec", ""),
        "status": req.get("status", ""),
        "result_count": req.get("result_count", 0),
        "created_at": _iso(req.get("created_at")),
        "completed_at": _iso(req.get("completed_at")),
        "results": results,
    }


def list_sourcing_requests(user_id: str | None, page: int = 1, page_size: int = 20) -> dict:
    from app.domains.sourcing.repo import list_requests
    return list_requests(user_id=user_id, page=page, page_size=page_size)


def add_supplier_to_library(data: dict) -> str:
    from app.domains.sourcing.supplier_repo import add_supplier
    sid = add_supplier(data)
    _rebuild_supplier_vector(sid, data["name"], data)
    return sid


# ---- access applications ----

def list_access_applications_svc(status: str | None = None, page: int = 1, page_size: int = 20) -> dict:
    return list_access_applications(status=status, page=page, page_size=page_size)


def approve_application(aid: str, reviewer_id: str) -> dict:
    app = get_access_application(aid)
    if not app:
        raise ValueError(f"准入申请不存在: {aid}")
    if app.get("status") != "pending":
        raise ValueError(f"该申请已处理，无法重复审批")
    approve_access_application(aid, reviewer_id)
    logger.info("access_application_approved", application_id=aid, reviewer=reviewer_id)
    return {"success": True, "application_id": aid, "status": "approved"}


def reject_application(aid: str, reviewer_id: str) -> dict:
    app = get_access_application(aid)
    if not app:
        raise ValueError(f"准入申请不存在: {aid}")
    if app.get("status") != "pending":
        raise ValueError(f"该申请已处理，无法重复审批")
    reject_access_application(aid, reviewer_id)
    logger.info("access_application_rejected", application_id=aid, reviewer=reviewer_id)
    return {"success": True, "application_id": aid, "status": "rejected"}


# ---- supplier management ----

def update_supplier_in_library(sid: str, data: dict) -> dict:
    from app.domains.sourcing.supplier_repo import get_supplier, update_supplier

    existing = get_supplier(sid)
    if not existing:
        raise ValueError(f"供应商不存在: {sid}")

    # Build update dict from non-None values
    update_data = {k: v for k, v in data.items() if v is not None}
    if not update_data:
        return existing

    update_supplier(sid, update_data)

    # Rebuild vector if embedding-relevant fields changed
    if any(k in update_data for k in ("name", "categories", "regions")):
        current = get_supplier(sid)
        if current:
            _rebuild_supplier_vector(sid, current.get("name", ""), current)

    updated = get_supplier(sid)
    if updated:
        updated["_id"] = str(updated.get("_id", sid))
    return updated or existing


def get_top_alternatives(company_name: str, top_k: int = 3) -> list[dict]:
    """Find Top-K alternatives without creating a sourcing request."""
    query_text = company_name
    candidates = _vector_search(query_text, top_k=15)
    if not candidates:
        return []

    # Exclude self
    candidates = [c for c in candidates
                  if c.get("supplier_name", "").lower() != company_name.lower()]
    if not candidates:
        return []

    risk_map = _batch_assess_risk(candidates)
    results = []
    for c in candidates[:top_k]:
        name = c["supplier_name"]
        risk = risk_map.get(name, {"risk_score": 50, "risk_level": "unknown"})
        results.append({
            "supplier_name": name,
            "match_score": round(c["match_score"], 3),
            "risk_score": risk.get("risk_score", 50),
            "risk_level": risk.get("risk_level", "unknown"),
        })
    return results


# ---- internal helpers ----

def _vector_search(query_text: str, top_k: int = 20) -> list[dict[str, Any]]:
    """PG vector cosine search in supplier_profiles."""
    embedding = encode_single(query_text)
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

    with get_cursor() as (conn, cur):
        cur.execute(
            """SELECT supplier_name, content, metadata,
                      1 - (embedding <=> %s::vector) AS similarity
               FROM supplier_profiles
               ORDER BY embedding <=> %s::vector
               LIMIT %s""",
            (vec_str, vec_str, top_k),
        )
        rows = cur.fetchall()
        return [
            {"supplier_name": row[0], "content": row[1], "metadata": row[2], "match_score": float(row[3])}
            for row in rows
        ]


def _search_feishu_snapshot_suppliers(req_doc: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Search formal Feishu snapshots when a current master snapshot is available.

    ``None`` means the formal read model is unavailable and preserves the legacy
    PostgreSQL vector path. An empty list is a valid formal search miss and must
    not silently fall back to stale local vectors.
    """
    if not settings.FEISHU_BITABLE_ENABLED:
        return None

    from app.domains.sourcing.supplier_repo import (
        search_for_sourcing_v2,
        has_current_feishu_supplier_snapshot,
    )

    if not has_current_feishu_supplier_snapshot():
        return None

    qualifications = req_doc.get("qualifications")
    if isinstance(qualifications, (list, tuple)):
        qualifications = ",".join(str(item) for item in qualifications if item)
    requirement = {
        "category": req_doc.get("category", ""),
        "specification": req_doc.get("spec", ""),
        "region": req_doc.get("region_required", ""),
        "qualifications": qualifications or "",
    }
    try:
        candidates = search_for_sourcing_v2(requirement)
    except Exception as exc:
        logger.warning("feishu_snapshot_search_failed", error=type(exc).__name__)
        return None
    return [
        {
            **candidate,
            "content": " ".join([
                str(candidate.get("supplier_name") or ""),
                *candidate.get("categories", []),
                *candidate.get("specifications", []),
                *candidate.get("regions", []),
            ]),
            "metadata": {
                "supplier_id": candidate.get("supplier_id"),
                "supplier_code": candidate.get("supplier_code"),
                "categories": candidate.get("categories", []),
                "source": candidate.get("source"),
            },
            "match_score": _snapshot_match_score(candidate, requirement),
        }
        for candidate in candidates
    ]


def _snapshot_match_score(candidate: dict[str, Any], requirement: dict[str, Any]) -> float:
    """Calculate transparent constraint coverage for non-vector snapshot matches."""
    requested_fields = {
        field
        for field in ("category", "specification", "region", "qualifications")
        if _requirement_values(requirement.get(field))
    }
    if not requested_fields:
        return 0.5
    matched_fields = {
        reason.split(":", 1)[0]
        for reason in candidate.get("match_reasons", [])
        if isinstance(reason, str) and ":" in reason
    }
    return round(len(requested_fields & matched_fields) / len(requested_fields), 3)


def _requirement_values(value: Any) -> list[str]:
    """Parse comma-separated sourcing constraints for transparent scoring."""
    if not isinstance(value, str):
        return []
    return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]


def _filter_category_candidates(
    candidates: list[dict[str, Any]], category: Any,
) -> list[dict[str, Any]]:
    """Apply a hard category gate after vector recall."""
    if not isinstance(category, str) or not category.strip():
        return candidates

    requested = _normalise_category_text(category)
    accepted_terms = _CATEGORY_ALIASES.get(requested, (requested,))
    return [
        candidate
        for candidate in candidates
        if any(
            _normalise_category_text(term) in _candidate_category_text(candidate)
            for term in accepted_terms
        )
    ]


def _candidate_category_text(candidate: dict[str, Any]) -> str:
    """Build searchable category text from snapshots and legacy PG records."""
    metadata = candidate.get("metadata")
    metadata_categories = metadata.get("categories", []) if isinstance(metadata, dict) else []
    if isinstance(metadata_categories, str):
        metadata_categories = [metadata_categories]
    capability_values = [
        value
        for item in candidate.get("capabilities", [])
        if isinstance(item, dict)
        for value in (
            item.get("category"),
            item.get("product_name"),
            *item.get("product_keywords", []),
        )
        if value
    ]
    values = [
        candidate.get("content", ""),
        *metadata_categories,
        *candidate.get("categories", []),
        *capability_values,
    ]
    return _normalise_category_text(" ".join(str(value) for value in values if value))


def _normalise_category_text(value: str) -> str:
    return "".join(value.casefold().split())


def _batch_assess_risk(candidates: list[dict]) -> dict[str, dict]:
    """从 MongoDB 快照快速读取已有风险数据（不调外部 API）。

    对寻源场景，避免 DeepSeek/Tianyancha API 调用导致超时。
    从未评估过的供应商返回默认值 50/unknown。
    """
    from app.db.mongo import get_db

    db = get_db()
    out: dict[str, dict] = {}
    names = [c["supplier_name"] for c in candidates]

    # 批量查询：一次 aggregate 取所有候选企业的最近快照
    pipeline = [
        {"$match": {"company_name": {"$in": names}}},
        {"$sort": {"checked_at": -1}},
        {"$group": {"_id": "$company_name", "risk_score": {"$first": "$risk_score"}, "risk_level": {"$first": "$risk_level"}}},
    ]
    try:
        for doc in db["alert_snapshots"].aggregate(pipeline):
            name = doc["_id"]
            out[name] = {
                "risk_score": doc.get("risk_score", 50),
                "risk_level": doc.get("risk_level", "unknown"),
                "summary": doc.get("risk_level", "未知"),
            }
    except Exception:
        pass  # 聚合失败则降级为默认值

    # 未找到快照的补充默认值
    for name in names:
        if name not in out:
            out[name] = {"risk_score": 50, "risk_level": "unknown", "summary": "未评估"}
    return out


def _rebuild_supplier_vector(sid: str, name: str, data: dict) -> None:
    """构建供应商向量并写入 PG。"""
    parts = [name]
    parts.extend(data.get("categories", []))
    parts.extend(data.get("regions", []))
    content = " ".join(parts)
    embedding = encode_single(content)
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

    with get_cursor() as (conn, cur):
        cur.execute(
            """INSERT INTO supplier_profiles (id, supplier_name, content, embedding, metadata)
               VALUES (%s, %s, %s, %s::vector, %s)
               ON CONFLICT (id) DO UPDATE SET
               content = EXCLUDED.content,
               embedding = EXCLUDED.embedding,
               metadata = EXCLUDED.metadata""",
            (sid, name, content, vec_str, "{}"),
        )


def _iso(dt) -> str:
    if dt is None:
        return ""
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)
