from fastapi import APIRouter

from app.services.macro_service import assess_macro_risk, get_industry_pmi
from app.services.alternative_service import find_alternatives, get_alternative_dashboard

router = APIRouter()


# ---- macro risk ----
# NOTE: /macro/pmi must come BEFORE /macro/{company_name} to avoid route conflict

@router.get("/macro/pmi")
async def latest_pmi():
    """获取最新 PMI 数据。"""
    pmi = get_industry_pmi()
    if pmi is None:
        return {"error": "PMI 数据获取失败"}
    return pmi


@router.get("/macro/{company_name}")
async def company_macro_risk(company_name: str):
    """评估企业的宏观风险（政策+地区+行业）。"""
    return assess_macro_risk(company_name)


# ---- alternatives ----

@router.get("/alternatives/{company_name}")
async def company_alternatives(company_name: str):
    """为企业推荐低风险替代供应商。"""
    return find_alternatives(company_name)


@router.get("/alternatives")
async def alternative_dashboard():
    """替代建议总览：所有高风险企业的替代方案。"""
    return get_alternative_dashboard()
