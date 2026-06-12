import asyncio

from fastapi import APIRouter

from app.schemas import RiskCalculateRequest, RiskCalculateResponse, RiskAssessRequest
from app.services.risk_service import assess_risk, calculate_risk

router = APIRouter()


@router.post("/calculate", response_model=RiskCalculateResponse)
async def risk_calculate(request: RiskCalculateRequest):
    return await asyncio.to_thread(calculate_risk, request)


@router.post("/assess", response_model=RiskCalculateResponse)
async def risk_assess(request: RiskAssessRequest):
    return await asyncio.to_thread(assess_risk, request)
