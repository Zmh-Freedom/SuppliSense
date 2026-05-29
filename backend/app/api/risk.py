from fastapi import APIRouter

from app.schemas import RiskCalculateRequest, RiskCalculateResponse
from app.services.risk_service import calculate_risk

router = APIRouter()


@router.post("/calculate", response_model=RiskCalculateResponse)
async def risk_calculate(request: RiskCalculateRequest):
    return calculate_risk(request)
