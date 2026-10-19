"""
Trend API routes -- risk score trend and alert frequency trend.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.core.logging import get_logger
from app.db.mongo import get_db

logger = get_logger(__name__)
router = APIRouter(prefix="/trend", tags=["trend"], dependencies=[Depends(get_current_user)])


@router.get(
    "/risk/{company_name}",
    summary="企业风险评分趋势",
    description="获取指定企业风险评分时间序列。从 PG 读取当前操作者、企业主体、范围和最新政策的可比结果。",
    responses={500: {"description": "服务器内部错误"}},
)
async def risk_trend(
    company_name: str,
    days: int = Query(90, ge=1, le=3650, description="查询天数"),
    current_user=Depends(get_current_user),
):
    import asyncio
    from app.domains.risk.assessment_service import comparable_trend
    from app.domains.risk.assessment_policy import SCORING_VERSION

    data = await asyncio.to_thread(comparable_trend, company_name, actor_id=current_user.id,
        actor_role=current_user.role.value, days=days)
    return {"company_name":company_name,"days":days,"scoring_version":SCORING_VERSION,
        "source":"postgresql","data":data}


@router.get(
    "/alert",
    summary="告警频率趋势",
    description="获取过去 N 天内每天的告警数量统计。",
    responses={500: {"description": "服务器内部错误"}},
)
async def alert_trend(
    days: int = Query(30, description="查询天数（默认 30 天）"),
    current_user=Depends(get_current_user),
):
    """Get alert frequency trend (count per day) over N days."""
    db = get_db()
    since = datetime.now(timezone.utc) - timedelta(days=days)

    from app.domains.alert.service import get_watchlist_targets

    visible_names = [
        target["company_name"]
        for target in get_watchlist_targets(current_user.id, current_user.role.value)
        if target.get("company_name")
    ]
    if not visible_names:
        return {"days": days, "data": []}

    pipeline = [
        {"$match": {"created_at": {"$gte": since}, "company_name": {"$in": visible_names}}},
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    result = list(db["alerts"].aggregate(pipeline))

    return {
        "days": days,
        "data": [{"date": r["_id"], "count": r["count"]} for r in result],
    }
