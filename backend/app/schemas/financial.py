from pydantic import BaseModel


class FinancialMetrics(BaseModel):
    # growth
    revenue_growth: float
    net_profit_growth: float
    # structural
    debt_ratio: float
    cash_flow: float
    # profitability
    roe: float = 0.0
    net_profit_margin: float = 0.0
    # liquidity
    current_ratio: float = 0.0
    quick_ratio: float = 0.0
    equity_ratio: float = 0.0  # 产权比率
    # efficiency
    inventory_turnover: float = 0.0
    ar_turnover_days: float = 0.0
    # quality
    recurring_profit_ratio: float = 0.0
    # trend (3-year slope, positive = improving)
    revenue_trend: float = 0.0
    net_profit_trend: float = 0.0
    debt_trend: float = 0.0
