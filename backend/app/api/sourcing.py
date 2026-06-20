"""Sourcing API routes — 智能寻源接口。"""

import asyncio
import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_user
from app.core.logging import get_logger
from app.schemas.sourcing import (
    SelectResultRequest,
    SourcingRequestInput,
    SourcingRequestResponse,
    SourcingResultItem,
    SourcingSearchResponse,
    SupplierInput,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/sourcing", tags=["sourcing"], dependencies=[Depends(get_current_user)])


@router.post(
    "/requests",
    response_model=SourcingRequestResponse,
    summary="创建寻源请求",
    description="提交采购需求，创建寻源请求。返回 request_id 用于后续搜索。",
)
async def create_request(req: SourcingRequestInput, request: Request):
    from app.services.sourcing_service import create_sourcing_request

    user_id = getattr(request.state, "user_id", "anonymous")
    rid = await asyncio.to_thread(create_sourcing_request, req, user_id)
    return SourcingRequestResponse(request_id=rid, status="created")


@router.post(
    "/requests/{request_id}/search",
    summary="执行寻源搜索（SSE 流式）",
    description="流式返回寻源进度和结果。事件: retrieving / assessing / ranking / sourcing_result / done / error。",
)
async def search_stream(request_id: str):
    from app.services.sourcing_service import search_suppliers
    from app.repositories.sourcing_repo import get_request as _get_req

    req = _get_req(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="寻源请求不存在")

    async def event_generator():
        from app.graphs.streaming import _sse_event

        yield _sse_event("session", {"request_id": request_id})

        try:
            yield _sse_event("retrieving", {"message": "正在检索候选供应商..."})
            yield _sse_event("assessing", {"message": "正在并行评估风险..."})

            result = await asyncio.to_thread(search_suppliers, request_id)

            yield _sse_event("ranking", {"message": f"找到 {len(result.get('results', []))} 个候选"})

            if result.get("results"):
                yield _sse_event("sourcing_result", {
                    "request_id": request_id,
                    "results": result["results"],
                })

            yield _sse_event("done", {"message": "寻源完成", "count": len(result.get("results", []))})
        except Exception as e:
            logger.error("sourcing_stream_error", request_id=request_id, error=str(e))
            yield _sse_event("error", {"message": str(e)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/requests",
    summary="寻源历史",
    description="获取当前用户的寻源请求历史。",
)
async def list_requests(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    from app.services.sourcing_service import list_sourcing_requests

    user_id = getattr(request.state, "user_id", None)
    return await asyncio.to_thread(list_sourcing_requests, user_id, page, page_size)


@router.get(
    "/requests/{request_id}",
    summary="寻源详情",
    description="获取单次寻源请求的详情和结果列表。",
)
async def get_request_detail(request_id: str):
    from app.services.sourcing_service import get_request_detail as _detail

    result = await asyncio.to_thread(_detail, request_id)
    if not result:
        raise HTTPException(status_code=404, detail="寻源请求不存在")
    return result


@router.post(
    "/results/{result_id}/select",
    summary="勾选寻源结果",
    description="对寻源结果执行动作: watchlist(加入监控) / apply_access(申请准入)。",
)
async def select_result(result_id: str, body: SelectResultRequest, request: Request):
    from app.services.sourcing_service import select_result as _select

    user_id = getattr(request.state, "user_id", "anonymous")
    return await asyncio.to_thread(_select, result_id, body.action, user_id)


@router.post(
    "/suppliers",
    summary="录入供应商",
    description="手动录入供应商到本地供应商库。",
)
async def add_supplier(body: SupplierInput):
    from app.services.sourcing_service import add_supplier_to_library

    sid = await asyncio.to_thread(add_supplier_to_library, body.model_dump())
    return {"supplier_id": sid, "status": "created"}


@router.get(
    "/suppliers",
    summary="供应商列表",
    description="查询本地供应商库。",
)
async def list_suppliers(
    keyword: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    from app.repositories.supplier_repo import list_suppliers as _list

    return await asyncio.to_thread(_list, keyword, status, page, page_size)
