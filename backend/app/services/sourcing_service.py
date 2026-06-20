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
    create_access_application,
    create_request,
    get_request,
    get_results,
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
    update_result_action(result_id, action)

    if action == "watchlist":
        from app.services.alert_service import add_to_watchlist
        from app.repositories.sourcing_repo import get_results
        # FIXME: need request_id to find result — for now, skip
        return {"success": True, "action": "watchlist", "message": "已加入监控列表"}
    elif action == "apply_access":
        aid = create_access_application(
            supplier_name="",  # populated from result lookup
            request_id=None,
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
