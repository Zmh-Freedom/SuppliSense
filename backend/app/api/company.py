import asyncio

from fastapi import APIRouter, Query

from app.repositories.company_repo import search_companies
from app.schemas.company import CompanyProfile
from app.schemas.risk import RiskInfo
from app.services.company_service import get_company_profile, get_company_risk

router = APIRouter()


@router.get(
    "/profile",
    response_model=CompanyProfile,
    summary="获取企业工商信息",
    description="根据企业名称查询企业的工商注册信息，包括法定代表人、注册资本、经营范围等。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def company_profile(company_name: str = Query(..., description="企业名称")):
    return await asyncio.to_thread(get_company_profile, company_name)


@router.get(
    "/risk",
    response_model=RiskInfo,
    summary="获取企业司法风险信息",
    description="根据企业名称查询企业的司法风险数据，包括涉诉数量、被执行次数、经营异常等。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def company_risk(company_name: str = Query(..., description="企业名称")):
    return await asyncio.to_thread(get_company_risk, company_name)


@router.get(
    "/search",
    summary="搜索企业",
    description="根据关键词搜索匹配的企业列表。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def company_search(keyword: str = Query(..., description="搜索关键词")):
    results = await asyncio.to_thread(search_companies, keyword)
    return {"keyword": keyword, "count": len(results), "results": results}
