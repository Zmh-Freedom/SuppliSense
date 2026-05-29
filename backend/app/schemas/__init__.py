from pydantic import BaseModel

from app.schemas.company import CompanyProfile
from app.schemas.risk import RiskInfo
from app.schemas.financial import FinancialMetrics


class RiskCalculateRequest(BaseModel):
    company: CompanyProfile
    risk: RiskInfo
    financial: FinancialMetrics | None = None

    # extra risk indicators beyond the basic schemas
    dishonesty_count: int = 0
    major_lawsuit: bool = False
    legal_person_change_frequent: bool = False
    net_profit_declining: bool = False
    revenue_declining: bool = False


class RiskCalculateResponse(BaseModel):
    risk_score: int
    risk_level: str
