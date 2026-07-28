"""Supplier API routes — master data management and profile aggregation."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import get_current_user
from app.core.logging import get_logger
from app.schemas.supplier import (
    SupplierListResponse,
    SupplierMasterResponse,
    SupplierProfileResponse,
    SupplierUpdateInput,
)

logger = get_logger()
router = APIRouter(
    prefix="/suppliers",
    tags=["supplier"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "",
    summary="供应商列表",
    description="分页查询供应商主数据，支持关键词、状态筛选。",
    responses={500: {"description": "服务器内部错误"}},
)
async def list_suppliers_endpoint(
    keyword: str = Query("", description="关键词（企业名模糊匹配）"),
    status: str = Query("", description="供应商状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页数量"),
):
    """List suppliers with pagination and optional filters."""
    import asyncio
    from app.domains.supplier.repo import list_suppliers

    data = await asyncio.to_thread(
        list_suppliers,
        keyword=keyword or None,
        status=status or None,
        page=page,
        page_size=page_size,
        hide_bare=False,  # profile endpoint shows all suppliers
    )

    return SupplierListResponse(
        items=data["items"],
        total=data["total"],
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{supplier_id}",
    summary="供应商主数据详情",
    description="获取单个供应商的完整主数据记录。",
    responses={404: {"description": "供应商不存在"}},
)
async def get_supplier_master(supplier_id: str):
    """Get the full master data record for a supplier."""
    import asyncio
    from app.domains.supplier.repo import get_supplier

    doc = await asyncio.to_thread(get_supplier, supplier_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"供应商 {supplier_id} 不存在")

    doc["_id"] = str(doc["_id"])
    return doc


@router.put(
    "/{supplier_id}",
    summary="更新供应商主数据",
    description="部分更新供应商字段，自动记录变更历史。",
    responses={404: {"description": "供应商不存在"}},
)
async def update_supplier_master(supplier_id: str, body: SupplierUpdateInput):
    """Update supplier master data (partial update)."""
    import asyncio
    from app.domains.supplier.repo import get_supplier, update_supplier

    existing = await asyncio.to_thread(get_supplier, supplier_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"供应商 {supplier_id} 不存在")

    data = body.model_dump(exclude_none=True)
    if not data:
        return {"message": "没有需要更新的字段"}

    await asyncio.to_thread(update_supplier, supplier_id, data)

    # Return updated document
    updated = await asyncio.to_thread(get_supplier, supplier_id)
    if updated:
        updated["_id"] = str(updated["_id"])
    return updated


@router.get(
    "/{supplier_id}/profile",
    summary="供应商画像",
    description="聚合所有领域数据（风险、财务、舆情、合规、ESG、告警、关系、变更日志），构建统一供应商画像。",
    responses={404: {"description": "供应商不存在"}},
)
async def get_supplier_profile(supplier_id: str):
    """Get the complete aggregated supplier profile."""
    import asyncio
    from app.domains.supplier.service import build_supplier_profile

    try:
        profile = await asyncio.to_thread(build_supplier_profile, supplier_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return profile


@router.get(
    "/{supplier_id}/changelog",
    summary="供应商变更日志",
    description="获取供应商主数据的变更审计历史。",
    responses={404: {"description": "供应商不存在"}},
)
async def get_supplier_changelog(
    supplier_id: str,
    limit: int = Query(20, ge=1, le=200, description="返回条数"),
):
    """Get the change log for a supplier."""
    import asyncio
    from app.domains.supplier.repo import get_changelog, get_supplier

    existing = await asyncio.to_thread(get_supplier, supplier_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"供应商 {supplier_id} 不存在")

    changelog = await asyncio.to_thread(get_changelog, supplier_id, limit)
    return {"supplier_id": supplier_id, "items": changelog, "total": len(changelog)}
