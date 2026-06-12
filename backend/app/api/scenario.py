import asyncio

from pydantic import BaseModel

from fastapi import APIRouter, Query

from app.services.scenario_service import simulate
from app.services.sanctions_service import check_sanctions, get_sanctions_dashboard

router = APIRouter()


# ---- scenario simulation ----

class ScenarioRequest(BaseModel):
    company_name: str
    scenario: str = "bankruptcy"  # bankruptcy | lawsuit | disruption | quality


@router.post("/scenario")
async def run_simulation(req: ScenarioRequest):
    """运行情景模拟。"""
    return await asyncio.to_thread(simulate, req.company_name, req.scenario)


@router.get("/scenario/{company_name}")
async def quick_simulation(company_name: str, scenario: str = Query("bankruptcy", description="情景类型")):
    """快速情景模拟。"""
    return await asyncio.to_thread(simulate, company_name, scenario)


# ---- sanctions ----

@router.get("/sanctions/{company_name}")
async def company_sanctions(company_name: str):
    """检查企业的国际制裁/黑名单状态。"""
    return await asyncio.to_thread(check_sanctions, company_name)


@router.get("/sanctions")
async def sanctions_dashboard():
    """制裁筛查总览。"""
    return await asyncio.to_thread(get_sanctions_dashboard)
