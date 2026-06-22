import asyncio

from pydantic import BaseModel

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.domains.risk.scenario_service import simulate
from app.domains.risk.sanctions_service import check_sanctions, get_sanctions_dashboard

router = APIRouter(dependencies=[Depends(get_current_user)])


# ---- scenario simulation ----

class ScenarioRequest(BaseModel):
    company_name: str
    scenario: str = "bankruptcy"  # bankruptcy | lawsuit | disruption | quality


@router.post(
    "/scenario",
    summary="运行情景模拟",
    description="模拟指定企业在 bankruptcy（破产）、lawsuit（诉讼）、disruption（供应中断）或 quality（质量问题）场景下的风险变化。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def run_simulation(req: ScenarioRequest):
    """运行情景模拟。"""
    return await asyncio.to_thread(simulate, req.company_name, req.scenario)


@router.get(
    "/scenario/{company_name}",
    summary="快速情景模拟",
    description="通过 GET 请求快速模拟指定企业在破产等默认情景下的风险变化。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def quick_simulation(company_name: str, scenario: str = Query("bankruptcy", description="情景类型")):
    """快速情景模拟。"""
    return await asyncio.to_thread(simulate, company_name, scenario)


# ---- sanctions ----

@router.get(
    "/sanctions/{company_name}",
    summary="检查企业制裁状态",
    description="检查指定企业是否在国际制裁/黑名单中。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def company_sanctions(company_name: str):
    """检查企业的国际制裁/黑名单状态。"""
    return await asyncio.to_thread(check_sanctions, company_name)


@router.get(
    "/sanctions",
    summary="制裁筛查总览面板",
    description="返回所有监控企业的制裁筛查结果总览。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def sanctions_dashboard():
    """制裁筛查总览。"""
    return await asyncio.to_thread(get_sanctions_dashboard)
