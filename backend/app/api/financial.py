from fastapi import APIRouter, Query

from app.schemas.financial import FinancialMetrics
from app.services.financial_service import get_financial_metrics

router = APIRouter()


@router.get("/metrics", response_model=FinancialMetrics)
async def financial_metrics(company_name: str = Query(..., description="企业名称")):
    return get_financial_metrics(company_name)
