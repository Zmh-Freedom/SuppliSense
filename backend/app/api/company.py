import asyncio

from fastapi import APIRouter, Query

from app.repositories.company_repo import search_companies
from app.schemas.company import CompanyProfile
from app.schemas.risk import RiskInfo
from app.services.company_service import get_company_profile, get_company_risk

router = APIRouter()


@router.get("/profile", response_model=CompanyProfile)
async def company_profile(company_name: str = Query(..., description="企业名称")):
    return await asyncio.to_thread(get_company_profile, company_name)


@router.get("/risk", response_model=RiskInfo)
async def company_risk(company_name: str = Query(..., description="企业名称")):
    return await asyncio.to_thread(get_company_risk, company_name)


@router.get("/search")
async def company_search(keyword: str = Query(..., description="搜索关键词")):
    results = await asyncio.to_thread(search_companies, keyword)
    return {"keyword": keyword, "count": len(results), "results": results}
