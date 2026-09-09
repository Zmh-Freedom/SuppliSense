"""Access application approval endpoints — admin only."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.deps import get_current_user, require_admin
from app.core.logging import get_logger
from app.schemas.sourcing import ApproveRejectRequest
from app.schemas.user import UserInDB

logger = get_logger(__name__)
router = APIRouter(
    prefix="/access-applications",
    tags=["sourcing"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "",
    summary="准入申请列表",
    description="管理员查看准入申请，支持按状态筛选。",
)
async def list_applications(
    request: Request,
    status: str | None = Query(None, description="pending/approved/rejected，不传=全部"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _current_user=Depends(require_admin),
):
    from app.domains.sourcing.service import list_access_applications_svc

    return await asyncio.to_thread(list_access_applications_svc, status, page, page_size)


@router.post(
    "/{application_id}/approve",
    summary="通过准入申请",
)
async def approve(
    application_id: str,
    _body: ApproveRejectRequest,
    _request: Request,
    current_user: UserInDB = Depends(require_admin),
):
    from app.domains.sourcing.service import approve_application

    try:
        return await asyncio.to_thread(approve_application, application_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{application_id}/reject",
    summary="拒绝准入申请",
)
async def reject(
    application_id: str,
    _body: ApproveRejectRequest,
    _request: Request,
    current_user: UserInDB = Depends(require_admin),
):
    from app.domains.sourcing.service import reject_application

    try:
        return await asyncio.to_thread(reject_application, application_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
