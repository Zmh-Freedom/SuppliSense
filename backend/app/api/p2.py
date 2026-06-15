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

@router.get(
    "/esg/{company_name}",
    summary="评估企业 ESG 风险",
    description="评估指定企业的环境（E）、社会（S）、治理（G）三个维度的风险评分。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def company_esg(company_name: str):
    """评估单个企业的 ESG 风险。"""
    result = await asyncio.to_thread(assess_esg, company_name)
    if result is None:
        return {"company_name": company_name, "error": "未找到企业数据"}
    return result


@router.get(
    "/esg",
    summary="ESG 总览面板",
    description="返回所有监控企业的 ESG 综合评分和分维度得分。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def esg_dashboard():
    """ESG 总览：所有监控企业的 ESG 评分。"""
    return {"companies": await asyncio.to_thread(assess_all_esg)}


# ---- Contagion ----

@router.get(
    "/contagion/{company_name}",
    summary="分析企业风险传染路径",
    description="分析指定企业在供应链网络中的风险传染路径和影响范围。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def company_contagion(company_name: str):
    """分析企业的风险传染路径。"""
    return await asyncio.to_thread(analyze_contagion, company_name)


@router.get(
    "/contagion/{company_name}/graph",
    summary="获取风险传染图谱数据",
    description="返回指定企业供应链网络的图谱可视化数据（nodes + edges）。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def company_contagion_graph(company_name: str):
    """返回图谱可视化所需的 nodes + edges 数据。"""
    return await asyncio.to_thread(get_graph_data, company_name)


@router.get(
    "/contagion",
    summary="风险传染总览面板",
    description="返回所有监控企业在供应链网络中的风险传染状态总览。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def contagion_dashboard():
    """风险传染总览。"""
    return await asyncio.to_thread(get_contagion_dashboard)


# ---- Dependencies ----

class DependencyRequest(BaseModel):
    supplier: str
    customer: str
    material: str = ""
    importance: str = "medium"


@router.get(
    "/dependencies/{company_name}",
    summary="获取企业供应链依赖关系",
    description="获取指定企业作为供应商或客户的所有上下游依赖关系。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def list_dependencies(company_name: str):
    """获取企业的供应链依赖关系。"""
    return {"company_name": company_name, "dependencies": await asyncio.to_thread(get_supply_dependencies, company_name)}


@router.post(
    "/dependencies",
    summary="添加供应链依赖关系",
    description="创建新的供应链上下游依赖关系，包含供应商、客户、物料和重要性等级。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def create_dependency(req: DependencyRequest):
    """添加供应链依赖关系。"""
    return await asyncio.to_thread(add_dependency, req.supplier, req.customer, req.material, req.importance)


@router.delete(
    "/dependencies",
    summary="删除供应链依赖关系",
    description="根据供应商、客户和物料信息删除指定的供应链依赖关系。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def delete_dependency(
    supplier: str = Query(..., description="供应商名称"),
    customer: str = Query(..., description="客户名称"),
    material: str = Query("", description="物料名称"),
):
    """删除供应链依赖关系。"""
    return await asyncio.to_thread(remove_dependency, supplier, customer, material)
