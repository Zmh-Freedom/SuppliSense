"""
Background task API routes (uses asyncio.to_thread, no Celery).
"""

import asyncio

from pydantic import BaseModel
from fastapi import APIRouter, Depends

from app.core.deps import require_admin_or_analyst
from app.schemas.user import UserInDB
from app.tasks.risk_tasks import (
    assess_risk_async,
    batch_refresh_all,
    check_all_async,
    refresh_company_async,
)
from app.tasks.sentiment_tasks import analyze_all_sentiment_async, analyze_sentiment_async

router = APIRouter(prefix="/async", tags=["async"])


class CompanyRequest(BaseModel):
    company_name: str


@router.post(
    "/assess",
    summary="评估风险",
    description="对企业进行风险评估。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def assess_async(
    req: CompanyRequest,
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    result = await asyncio.to_thread(assess_risk_async, req.company_name)
    return result


@router.post(
    "/refresh",
    summary="刷新天眼查数据",
    description="刷新企业天眼查数据（付费功能）。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def refresh_async(
    req: CompanyRequest,
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    result = await asyncio.to_thread(refresh_company_async, req.company_name)
    return result


@router.post(
    "/refresh-all",
    summary="批量刷新天眼查数据",
    description="批量刷新所有监控企业的天眼查数据（付费功能）。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def refresh_all_async(
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    result = await asyncio.to_thread(batch_refresh_all)
    return result


@router.post(
    "/check-all",
    summary="批量财务检查",
    description="对所有监控企业进行财务数据检查。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def check_all_async_endpoint(
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    result = await asyncio.to_thread(check_all_async)
    return result


@router.post(
    "/sentiment",
    summary="舆情分析",
    description="对指定企业进行舆情情感分析。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def sentiment_async(
    req: CompanyRequest,
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    result = await asyncio.to_thread(analyze_sentiment_async, req.company_name)
    return result


@router.post(
    "/sentiment-all",
    summary="批量舆情分析",
    description="对所有监控企业进行舆情情感分析。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def sentiment_all_async(
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    result = await asyncio.to_thread(analyze_all_sentiment_async)
    return result
