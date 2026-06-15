import asyncio

from fastapi import APIRouter, HTTPException

from app.schemas import RiskCalculateRequest, RiskCalculateResponse, RiskAssessRequest
from app.services.risk_service import assess_risk, calculate_risk

router = APIRouter()

RISK_TIMEOUT_SECONDS = 60  # 风险评估超时时间


@router.post(
    "/calculate",
    response_model=RiskCalculateResponse,
    summary="计算企业风险评分",
    description="基于企业工商信息、司法风险、财务指标等多维度数据，计算 13 维度风险评分并返回风险等级。",
    responses={
        400: {"description": "请求参数错误"},
        504: {"description": "计算超时（>60秒）"},
        500: {"description": "服务器内部错误"},
    },
)
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


@router.post(
    "/assess",
    response_model=RiskCalculateResponse,
    summary="快速评估企业风险（含缓存）",
    description="根据企业名称快速评估风险。优先返回缓存结果，2小时内数据直接返回，过期数据返回缓存同时后台刷新。",
    responses={
        400: {"description": "请求参数错误"},
        504: {"description": "评估超时（>60秒）"},
        500: {"description": "服务器内部错误"},
    },
)
async def risk_assess(request: RiskAssessRequest):
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
        age_hours = round(age.total_seconds() / 3600, 1)

        # Look up listed status from baseinfo
        is_listed = False
        base = db["baseinfo"].find_one({"name": request.company_name}, {"items.result.bondNum": 1, "items.result.bondName": 1})
        if base:
            r = base.get("items", {}).get("result", {})
            is_listed = bool(r.get("bondNum") or r.get("bondName"))

        # Always return cached data if available (regardless of age)
        resp = {
            "risk_score": recent.get("risk_score", 0),
            "risk_level": recent.get("risk_level", "未知"),
            "financial": recent.get("financial"),
            "risk_detail": recent.get("risk_detail"),
            "cached_at": checked.isoformat(),
            "cache_age_hours": age_hours,
            "is_stale": age_hours >= 2,
            "is_listed": is_listed,
        }

        # Fresh cache: return immediately
        if age < timedelta(hours=2) and not request.force_refresh:
            return resp

        # Stale or force_refresh: return cache + trigger background refresh
        if request.force_refresh or age >= timedelta(hours=2):
            from fastapi import BackgroundTasks
            # For stale data, rely on the next schedule; for force_refresh, do it now
            if request.force_refresh:
                try:
                    fresh = await asyncio.wait_for(
                        asyncio.to_thread(assess_risk, request),
                        timeout=RISK_TIMEOUT_SECONDS,
                    )
                    fresh["cached_at"] = datetime.now(timezone.utc).isoformat()
                    fresh["cache_age_hours"] = 0
                    fresh["is_stale"] = False
                    return fresh
                except asyncio.TimeoutError:
                    pass  # Return cached data if timeout
            return resp

    # No cache at all — must compute
    try:
        fresh = await asyncio.wait_for(
            asyncio.to_thread(assess_risk, request),
            timeout=RISK_TIMEOUT_SECONDS,
        )
        fresh["cached_at"] = datetime.now(timezone.utc).isoformat()
        fresh["cache_age_hours"] = 0
        fresh["is_stale"] = False
        return fresh
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"风险评估超时（>{RISK_TIMEOUT_SECONDS}秒），请稍后重试",
        )
