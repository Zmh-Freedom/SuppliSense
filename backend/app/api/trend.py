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
    description="获取指定企业风险评分时间序列。优先从 PG 读取并按 scoring_version 过滤，确保新旧评分体系不混淆。",
    responses={500: {"description": "服务器内部错误"}},
)
async def risk_trend(
    company_name: str,
    days: int = Query(90, description="查询天数（默认 90 天）"),
):
    """
    风险评分趋势。

    优先从 PG assessment_history 读取，按最新 scoring_version 过滤。
    PG 不可用时回退到 MongoDB alert_snapshots。
    """
    from app.services.risk_service import SCORING_VERSION

    # 优先 PG
    try:
        from app.repositories.assessment_repo import get_trend as pg_get_trend

        data = pg_get_trend(company_name, days=days, scoring_version=SCORING_VERSION)
        if data:
            return {
                "company_name": company_name,
                "days": days,
                "scoring_version": SCORING_VERSION,
                "source": "postgresql",
                "data": data,
            }
    except Exception as e:
        logger.warning("trend_pg_fallback", company=company_name, error=str(e))

    # 回退 MongoDB
    db = get_db()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    snapshots = list(
        db["alert_snapshots"]
        .find(
            {"company_name": company_name, "checked_at": {"$gte": since}},
            {"checked_at": 1, "risk_score": 1, "risk_level": 1, "_id": 0},
        )
        .sort("checked_at", 1)
    )

    return {
        "company_name": company_name,
        "days": days,
        "scoring_version": "unknown",
        "source": "mongodb",
        "data": [
            {
                "date": s["checked_at"].strftime("%Y-%m-%d"),
                "risk_score": s.get("risk_score", 0),
                "risk_level": s.get("risk_level", ""),
            }
            for s in snapshots
        ],
    }


@router.get(
    "/alert",
    summary="告警频率趋势",
    description="获取过去 N 天内每天的告警数量统计。",
    responses={500: {"description": "服务器内部错误"}},
)
async def alert_trend(
    days: int = Query(30, description="查询天数（默认 30 天）"),
):
    """Get alert frequency trend (count per day) over N days."""
    db = get_db()
    since = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline = [
        {"$match": {"created_at": {"$gte": since}}},
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
