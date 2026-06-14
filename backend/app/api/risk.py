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
    # Try cache first
    from datetime import datetime, timedelta, timezone
    from app.db.mongo import get_db

    db = get_db()
    recent = db["alert_snapshots"].find_one(
        {"company_name": request.company_name},
        sort=[("checked_at", -1)],
    )
    if recent and recent.get("checked_at"):
        checked = recent["checked_at"]
        if isinstance(checked, str):
            checked = datetime.fromisoformat(checked.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - checked.replace(tzinfo=timezone.utc) if checked.tzinfo is None else datetime.now(timezone.utc) - checked
        # If cached within 2 hours, return immediately and refresh in background
        if age < timedelta(hours=2):
            resp = {
                "risk_score": recent.get("risk_score", 0),
                "risk_level": recent.get("risk_level", "未知"),
                "financial": recent.get("financial"),
                "risk_detail": recent.get("risk_detail"),
            }
            return resp

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
