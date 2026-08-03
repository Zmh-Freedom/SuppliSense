"""FastAPI routes for Company Identity operations."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user, require_admin, require_admin_or_analyst
from app.core.errors import DomainError
from app.domains.company.service import (
    create_company,
    get_company,
    merge_company,
    search_identity,
    update_company,
    verify_company,
)
from app.schemas.company import (
    CompanyCreateInput,
    CompanyMergeInput,
    CompanyResponse,
    CompanyUpdateInput,
    CompanyVerifyInput,
    IdentityResolutionResponse,
)
from app.schemas.user import UserInDB

router = APIRouter(
    prefix="/companies",
    tags=["companies"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "/search",
    response_model=IdentityResolutionResponse,
    summary="搜索企业身份主体",
    description="按统一社会信用代码、法定名称、别名或前缀确定性搜索企业身份主体。",
    responses={401: {"description": "未认证"}, 422: {"description": "请求参数校验失败"}},
)
async def search_companies(
    q: str = Query(..., min_length=1, max_length=255, description="企业查询文本"),
    limit: int = Query(10, ge=1, le=100, description="最大候选数"),
) -> IdentityResolutionResponse:
    """Search identity subjects for any authenticated user."""
    result = await asyncio.to_thread(search_identity, q, limit)
    return IdentityResolutionResponse.model_validate(result)


@router.get(
    "/{company_id}",
    response_model=CompanyResponse,
    summary="获取企业身份主体",
    description="获取企业规范主体；已合并 ID 会返回其规范主体和重定向来源。",
    responses={401: {"description": "未认证"}, 404: {"description": "企业不存在"}},
)
async def get_company_endpoint(company_id: UUID) -> CompanyResponse:
    """Get a canonical company for any authenticated user."""
    company = await asyncio.to_thread(get_company, str(company_id))
    if company is None:
        raise DomainError("COMPANY_NOT_FOUND", "企业不存在", 404)

    response_data = dict(company)
    response_data["company_id"] = response_data["id"]
    return CompanyResponse.model_validate(response_data)


@router.post(
    "",
    summary="创建企业身份主体",
    description="管理员或分析师创建待核验企业身份主体。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "没有创建权限"},
        409: {"description": "企业统一社会信用代码已存在"},
        422: {"description": "请求参数校验失败"},
    },
)
async def create_company_endpoint(
    data: CompanyCreateInput,
    current_user: UserInDB = Depends(require_admin_or_analyst),
) -> dict[str, object]:
    """Create a company using only the authenticated actor identity."""
    return await asyncio.to_thread(
        create_company,
        data,
        current_user.id,
        current_user.role.value,
    )


@router.patch(
    "/{company_id}",
    summary="更新企业身份主体",
    description="管理员或分析师按版本更新企业身份主体。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "没有更新权限"},
        404: {"description": "企业不存在"},
        409: {"description": "企业版本冲突或已合并"},
        422: {"description": "请求参数校验失败"},
    },
)
async def update_company_endpoint(
    company_id: UUID,
    data: CompanyUpdateInput,
    current_user: UserInDB = Depends(require_admin_or_analyst),
) -> dict[str, object]:
    """Update a company using only the authenticated actor identity."""
    return await asyncio.to_thread(
        update_company,
        str(company_id),
        data,
        current_user.id,
        current_user.role.value,
    )


@router.post(
    "/{company_id}/verify",
    summary="核验企业身份主体",
    description="管理员核验企业身份并记录核验来源。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "需要管理员权限"},
        404: {"description": "企业不存在"},
        409: {"description": "企业版本冲突或统一社会信用代码冲突"},
        422: {"description": "请求参数校验失败"},
    },
)
async def verify_company_endpoint(
    company_id: UUID,
    data: CompanyVerifyInput,
    current_user: UserInDB = Depends(require_admin),
) -> dict[str, object]:
    """Verify a company using only the authenticated administrator identity."""
    return await asyncio.to_thread(
        verify_company,
        str(company_id),
        data,
        current_user.id,
        current_user.role.value,
    )


@router.post(
    "/{company_id}/merge",
    summary="合并企业身份主体",
    description="管理员确认后将源企业逻辑合并到目标规范主体。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "需要管理员权限"},
        404: {"description": "企业不存在"},
        409: {"description": "企业版本冲突、已合并或数据完整性错误"},
        422: {"description": "请求参数校验失败或未确认合并"},
    },
)
async def merge_company_endpoint(
    company_id: UUID,
    data: CompanyMergeInput,
    current_user: UserInDB = Depends(require_admin),
) -> dict[str, object]:
    """Merge a company using only the authenticated administrator identity."""
    return await asyncio.to_thread(
        merge_company,
        str(company_id),
        data,
        current_user.id,
        current_user.role.value,
    )
