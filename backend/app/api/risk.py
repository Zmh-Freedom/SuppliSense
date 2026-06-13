import asyncio

from fastapi import APIRouter, HTTPException

from app.schemas import RiskCalculateRequest, RiskCalculateResponse, RiskAssessRequest
from app.services.risk_service import assess_risk, calculate_risk

router = APIRouter()

RISK_TIMEOUT_SECONDS = 60  # 风险评估超时时间


@router.post("/calculate", response_model=RiskCalculateResponse)
async def risk_calculate(request: RiskCalculateRequest):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(calculate_risk, request),
            timeout=RISK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"风险评估超时（>{RISK_TIMEOUT_SECONDS}秒），请稍后重试",
        )


@router.post("/assess", response_model=RiskCalculateResponse)
async def risk_assess(request: RiskAssessRequest):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(assess_risk, request),
            timeout=RISK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"风险评估超时（>{RISK_TIMEOUT_SECONDS}秒），请稍后重试",
        )
