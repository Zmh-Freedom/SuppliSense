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
    guarantee_count: int = 0
    pledge_count: int = 0
    bankruptcy_count: int = 0
    env_penalty_count: int = 0


class RiskCalculateResponse(BaseModel):
    risk_score: int
    risk_level: str
    financial: FinancialMetrics | None = None
    risk_detail: dict | None = None
    score_breakdown: dict | None = None


class RiskAssessRequest(BaseModel):
    company_name: str
