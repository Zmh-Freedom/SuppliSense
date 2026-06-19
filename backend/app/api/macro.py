import asyncio

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.services.macro_service import assess_macro_risk, get_industry_pmi
from app.services.alternative_service import find_alternatives, get_alternative_dashboard

router = APIRouter(dependencies=[Depends(get_current_user)])


# ---- macro risk ----
# NOTE: /macro/pmi must come BEFORE /macro/{company_name} to avoid route conflict

@router.get(
    "/macro/pmi",
    summary="获取最新 PMI 数据",
    description="获取最新的采购经理人指数（PMI）数据，用于宏观经济形势判断。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def latest_pmi():
    """获取最新 PMI 数据。"""
    pmi = await asyncio.to_thread(get_industry_pmi)
    if pmi is None:
        return {"error": "PMI 数据获取失败"}
    return pmi


@router.get(
    "/macro/{company_name}",
    summary="评估企业宏观风险",
    description="综合评估企业的宏观风险，包括政策风险、地区风险和行业风险三个维度。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def company_macro_risk(company_name: str):
    """评估企业的宏观风险（政策+地区+行业）。"""
    return await asyncio.to_thread(assess_macro_risk, company_name)


# ---- alternatives ----

@router.get(
    "/alternatives/{company_name}",
    summary="推荐替代供应商",
    description="为指定企业推荐低风险的替代供应商。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def company_alternatives(company_name: str):
    """为企业推荐低风险替代供应商。"""
    return await asyncio.to_thread(find_alternatives, company_name)


@router.get(
    "/alternatives",
    summary="替代建议总览面板",
    description="返回所有高风险企业的替代供应商建议列表。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def alternative_dashboard():
    """替代建议总览：所有高风险企业的替代方案。"""
    return await asyncio.to_thread(get_alternative_dashboard)
