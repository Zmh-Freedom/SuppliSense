"""Authenticated, versioned screening read API."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.domains.risk.assessment_contracts import assessment_scope
from app.domains.risk.sanctions_service import analyze_sanctions
from app.schemas.screening import ScreeningReadResponse
from app.schemas.user import UserInDB


router = APIRouter(
    tags=["screening"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "/sanctions-screenings/{company_name}",
    response_model=ScreeningReadResponse,
    summary="读取名单筛查状态",
    description="返回受权主体范围内的名单筛查状态、来源覆盖和国内事实；来源缺失作为 unavailable 状态返回。",
    responses={401: {"description": "未登录"}, 403: {"description": "无权访问主体"}, 409: {"description": "主体未核验或存在歧义"}},
)
async def read_sanctions_screening(
    company_name: str,
    current_user: UserInDB = Depends(get_current_user),
) -> ScreeningReadResponse:
    with assessment_scope(
        actor_id=current_user.id,
        actor_role=current_user.role.value,
    ):
        result = await asyncio.to_thread(analyze_sanctions, company_name)
    if not result.get("entity_id"):
        raise HTTPException(status_code=409, detail="主体尚未唯一核验，无法读取名单筛查状态")
    return ScreeningReadResponse.model_validate(result)
