"""Sourcing API routes — 智能寻源接口。"""

import asyncio
import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_user, require_admin_or_analyst
from app.core.logging import get_logger
from app.schemas.sourcing import (
    SelectResultRequest,
    SourcingRequestInput,
    SourcingRequestResponse,
    SourcingResultItem,
    SourcingSearchResponse,
    SupplierInput,
    SupplierUpdateInput,
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
    from app.domains.sourcing.service import create_sourcing_request

    user_id = getattr(request.state, "user_id", "anonymous")
    rid = await asyncio.to_thread(create_sourcing_request, req, user_id)
    return SourcingRequestResponse(request_id=rid, status="created")


@router.post(
    "/requests/{request_id}/search",
    summary="执行寻源搜索（SSE 流式）",
    description="流式返回寻源进度和结果。事件: retrieving / assessing / ranking / sourcing_result / done / error。",
)
async def search_stream(request_id: str):
    from app.domains.sourcing.service import search_suppliers
    from app.domains.sourcing.repo import get_request as _get_req

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

            if result.get("results") or result.get("external_candidates"):
                yield _sse_event("sourcing_result", {
                    "request_id": request_id,
                    "results": result["results"],
                    "external_candidates": result.get("external_candidates", []),
                    "external_status": result.get("external_status", "not_required"),
                    "external_failure_reasons": result.get("external_failure_reasons", []),
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
    from app.domains.sourcing.service import list_sourcing_requests

    user_id = getattr(request.state, "user_id", None)
    return await asyncio.to_thread(list_sourcing_requests, user_id, page, page_size)


@router.get(
    "/requests/{request_id}",
    summary="寻源详情",
    description="获取单次寻源请求的详情和结果列表。",
)
async def get_request_detail(request_id: str):
    from app.domains.sourcing.service import get_request_detail as _detail

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
    from app.domains.sourcing.service import select_result as _select

    user_id = getattr(request.state, "user_id", "anonymous")
    return await asyncio.to_thread(_select, result_id, body.action, user_id)


@router.post(
    "/suppliers",
    summary="录入供应商",
    description="手动录入供应商到本地供应商库。",
)
async def add_supplier(body: SupplierInput):
    from app.domains.sourcing.service import add_supplier_to_library

    sid = await asyncio.to_thread(add_supplier_to_library, body.model_dump())
    return {"supplier_id": sid, "status": "created"}


@router.post(
    "/suppliers/sync",
    summary="同步飞书正式供应商",
    description="只读读取飞书多维表格并更新本地供应商快照，不会向飞书写入任何数据。",
    dependencies=[Depends(require_admin_or_analyst)],
)
async def sync_supplier_master():
    from app.services.feishu_bitable import FeishuBitableError, sync_supplier_tables as _sync

    try:
        return await asyncio.to_thread(_sync)
    except FeishuBitableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put(
    "/suppliers/{supplier_id}",
    summary="编辑供应商",
    description="编辑供应商资料，变更后将按最新主数据参与本地关键词匹配。",
)
async def update_supplier(supplier_id: str, body: SupplierUpdateInput):
    from app.domains.sourcing.service import update_supplier_in_library

    try:
        return await asyncio.to_thread(
            update_supplier_in_library,
            supplier_id,
            body.model_dump(exclude_unset=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/suppliers",
    summary="供应商列表",
    description="查询供应商主数据只读视图。启用飞书同步后优先查询飞书本地快照；hide_bare 默认 true。",
)
async def list_suppliers(
    keyword: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    hide_bare: bool = Query(True, description="是否隐藏自动创建且无品类/地域的空壳记录"),
):
    from app.domains.sourcing.supplier_repo import list_suppliers as _list

    return await asyncio.to_thread(_list, keyword, status, page, page_size, hide_bare)


@router.post(
    "/suppliers/import",
    summary="批量导入供应商",
    description="上传 Excel 文件批量导入供应商。仅管理员和分析师可操作。",
)
async def import_suppliers(
    file: UploadFile = File(...),
    _current_user=Depends(require_admin_or_analyst),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="未选择文件")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ("xlsx", "xls"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xls 格式")

    try:
        content = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="文件读取失败")

    from app.domains.sourcing.import_service import import_suppliers_from_excel

    return await asyncio.to_thread(import_suppliers_from_excel, content, file.filename)


@router.post(
    "/suppliers/import-tianyancha",
    summary="从天眼查搜索导入",
    description="按关键词/行业/地域从天眼查搜索企业并批量导入。仅管理员和分析师可操作。",
)
async def import_from_tianyancha(
    keyword: str = Query("", description="搜索关键词"),
    industry: str = Query("", description="行业分类"),
    region: str = Query("", description="地域"),
    max_results: int = Query(50, ge=1, le=100, description="最大导入数"),
    _current_user=Depends(require_admin_or_analyst),
):
    from app.domains.sourcing.import_service import import_from_tianyancha_search

    return await asyncio.to_thread(
        import_from_tianyancha_search,
        keyword=keyword,
        industry=industry,
        region=region,
        max_results=max_results,
    )


@router.post(
    "/external-candidates/{candidate_id}/verify",
    summary="核验外部候选",
    description="按需调用天眼查核验单个外部候选主体与风险。核验结果仍不会写入正式供应商主数据。",
)
async def verify_external_candidate(candidate_id: str):
    from app.domains.sourcing.service import verify_external_candidate as _verify

    try:
        return await asyncio.to_thread(_verify, candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
