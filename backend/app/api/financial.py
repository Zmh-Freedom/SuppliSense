import asyncio

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.schemas.financial import FinancialMetrics
from app.services.financial_service import get_financial_metrics

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get(
    "/metrics",
    response_model=FinancialMetrics,
    summary="获取企业财务指标",
    description="根据企业名称获取 15 项关键财务指标，包括营收、净利润、资产负债率、流动比率等。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def financial_metrics(company_name: str = Query(..., description="企业名称")):
    return await asyncio.to_thread(get_financial_metrics, company_name)
