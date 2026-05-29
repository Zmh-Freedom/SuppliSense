from pydantic import BaseModel


class FinancialMetrics(BaseModel):
    revenue_growth: float
    net_profit_growth: float
    debt_ratio: float
    cash_flow: float
