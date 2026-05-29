from fastapi import HTTPException

from app.repositories.company_repo import get_baseinfo
from app.repositories.financial_repo import get_financial_metrics as repo_get_metrics
from app.schemas.financial import FinancialMetrics


def get_financial_metrics(company_name: str) -> FinancialMetrics:
    profile = get_baseinfo(company_name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"企业 '{company_name}' 未找到")
    if not profile.is_listed:
        raise HTTPException(status_code=404, detail=f"'{company_name}' 为非上市公司，无财报数据")

    metrics = repo_get_metrics(company_name)
    if metrics is None:
        raise HTTPException(status_code=404, detail=f"'{company_name}' 暂无财报数据")
    return metrics
