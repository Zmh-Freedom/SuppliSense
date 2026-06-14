import asyncio

from pydantic import BaseModel

from fastapi import APIRouter, Query

from app.services.esg_service import assess_all_esg, assess_esg
from app.services.contagion import (
    add_dependency,
    analyze_contagion,
    get_contagion_dashboard,
    get_graph_data,
    get_supply_dependencies,
    remove_dependency,
)

router = APIRouter()


# ---- ESG ----

@router.get("/esg/{company_name}")
async def company_esg(company_name: str):
    """评估单个企业的 ESG 风险。"""
    result = await asyncio.to_thread(assess_esg, company_name)
    if result is None:
        return {"company_name": company_name, "error": "未找到企业数据"}
    return result


@router.get("/esg")
async def esg_dashboard():
    """ESG 总览：所有监控企业的 ESG 评分。"""
    return {"companies": await asyncio.to_thread(assess_all_esg)}


# ---- Contagion ----

@router.get("/contagion/{company_name}")
async def company_contagion(company_name: str):
    """分析企业的风险传染路径。"""
    return await asyncio.to_thread(analyze_contagion, company_name)


@router.get("/contagion/{company_name}/graph")
async def company_contagion_graph(company_name: str):
    """返回图谱可视化所需的 nodes + edges 数据。"""
    return await asyncio.to_thread(get_graph_data, company_name)


@router.get("/contagion")
async def contagion_dashboard():
    """风险传染总览。"""
    return await asyncio.to_thread(get_contagion_dashboard)


# ---- Dependencies ----

class DependencyRequest(BaseModel):
    supplier: str
    customer: str
    material: str = ""
    importance: str = "medium"


@router.get("/dependencies/{company_name}")
async def list_dependencies(company_name: str):
    """获取企业的供应链依赖关系。"""
    return {"company_name": company_name, "dependencies": await asyncio.to_thread(get_supply_dependencies, company_name)}


@router.post("/dependencies")
async def create_dependency(req: DependencyRequest):
    """添加供应链依赖关系。"""
    return await asyncio.to_thread(add_dependency, req.supplier, req.customer, req.material, req.importance)


@router.delete("/dependencies")
async def delete_dependency(
    supplier: str = Query(...),
    customer: str = Query(...),
    material: str = Query(""),
):
    """删除供应链依赖关系。"""
    return await asyncio.to_thread(remove_dependency, supplier, customer, material)
