"""
Sourcing service — 智能寻源业务逻辑。

框架无关，service 层不引入 LangGraph 依赖。
同步函数，异步并发在内部用 asyncio.run() 包装。
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.postgres import get_cursor
from app.repositories.company_repo import get_baseinfo
from app.repositories.sourcing_repo import (
    approve_access_application,
    create_access_application,
    create_request,
    get_access_application,
    get_request,
    get_result,
    get_results,
    list_access_applications,
    reject_access_application,
    save_result,
    update_request_status,
    update_result_action,
)
from app.schemas.sourcing import SourcingRequestInput
from app.services.embedding import encode_single

logger = get_logger(__name__)

RISK_TIMEOUT = 30  # seconds per supplier


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

    # 2. 向量检索
    candidates = _vector_search(query_text, top_k=20)
    if not candidates:
        update_request_status(request_id, "done", 0)
        return {"request_id": request_id, "status": "done", "results": [], "message": "本地供应商库未找到匹配结果"}

    # 3. 并发风险评估
    risk_map = _batch_assess_risk(candidates)

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
        item = {
            "result_id": rid,
            "supplier_name": name,
            "match_score": round(match_score, 3),
            "risk_score": risk_score,
            "risk_level": risk_level,
            "final_rank": round(final_rank, 3),
            "match_reason": risk_info.get("summary", ""),
            "risk_summary": risk_info.get("summary", ""),
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
            "supplier_name": r["supplier_name"],
            "match_score": r["match_score"],
            "risk_score": r["risk_score"],
            "risk_level": r["risk_level"],
            "final_rank": r["final_rank"],
            "match_reason": r["match_reason"],
            "risk_summary": r["risk_summary"],
        })

    update_request_status(request_id, "done", len(top10))
    logger.info("sourcing_complete", request_id=request_id, results=len(top10))

    return {
        "request_id": request_id,
        "status": "done",
        "results": [{
            "result_id": r["result_id"],
            "supplier_name": r["supplier_name"],
            "match_score": r["match_score"],
            "risk_score": r["risk_score"],
            "risk_level": r["risk_level"],
            "final_rank": r["final_rank"],
            "match_reason": r["match_reason"],
            "risk_summary": r["risk_summary"],
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
        from app.services.alert_service import add_to_watchlist
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
    from app.repositories.sourcing_repo import list_requests
    return list_requests(user_id=user_id, page=page, page_size=page_size)


def add_supplier_to_library(data: dict) -> str:
    from app.repositories.supplier_repo import add_supplier
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
    from app.repositories.supplier_repo import get_supplier, update_supplier

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


def _batch_assess_risk(candidates: list[dict]) -> dict[str, dict]:
    """并发评估候选供应商的风险，单家 30s 超时降级。"""

    async def _assess_one(name: str) -> tuple[str, dict | None]:
        try:
            from app.schemas import RiskAssessRequest
            from app.services.risk_service import assess_risk

            result = await asyncio.wait_for(
                asyncio.to_thread(assess_risk, RiskAssessRequest(company_name=name)),
                timeout=RISK_TIMEOUT,
            )
            return name, {
                "risk_score": result.risk_score,
                "risk_level": result.risk_level,
                "summary": result.risk_level,
            }
        except (asyncio.TimeoutError, Exception):
            return name, None

    async def _batch():
        tasks = [_assess_one(c["supplier_name"]) for c in candidates]
        results = await asyncio.gather(*tasks)
        return dict(results)

    raw = asyncio.run(_batch())

    # 降级处理
    out = {}
    for name, info in raw.items():
        if info is None:
            out[name] = {"risk_score": 50, "risk_level": "unknown", "summary": "暂未获取"}
            logger.warning("sourcing_risk_timeout", supplier=name)
        else:
            out[name] = info
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
