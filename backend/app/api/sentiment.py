import asyncio

from pydantic import BaseModel

from fastapi import APIRouter, BackgroundTasks, Query

from app.services.sentiment import (
    analyze_all_sentiment,
    analyze_sentiment,
    analyze_sentiment_background,
    get_sentiment_dashboard,
    get_sentiment_trend,
)

router = APIRouter()


class AnalyzeRequest(BaseModel):
    company_name: str
    force_refresh: bool = False


@router.get("/{company_name}")
async def company_sentiment(company_name: str, background_tasks: BackgroundTasks):
    """获取企业舆情分析结果。
    - 有缓存：立即返回（即使已过期），后台异步刷新
    - 无缓存：立即返回 analyzing 状态，后台异步分析
    """
    from app.services.sentiment import _get_cached_sentiment

    cached = _get_cached_sentiment(company_name)

    if cached:
        # 有缓存数据，立即返回；如果已过期则后台刷新
        if cached.get("is_stale", False):
            background_tasks.add_task(analyze_sentiment_background, company_name)
            cached["refreshing"] = True
        return cached

    # 无缓存，启动后台分析，立即返回"分析中"状态
    background_tasks.add_task(analyze_sentiment_background, company_name)
    return {
        "company_name": company_name,
        "has_data": False,
        "analyzing": True,
        "message": "正在分析舆情数据，请稍后刷新…",
    }


@router.get("/{company_name}/trend")
async def sentiment_trend(company_name: str):
    """获取企业舆情趋势（最近 7 次）。"""
    return await asyncio.to_thread(get_sentiment_trend, company_name)


@router.post("/analyze")
async def trigger_analysis(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    """触发企业舆情分析。
    - force_refresh=false: 返回缓存（如有），后台刷新过期数据
    - force_refresh=true: 立即返回缓存，后台强制刷新
    """
    from app.services.sentiment import _get_cached_sentiment

    if req.force_refresh:
        # 强制刷新：返回现有缓存 + 后台强制分析
        cached = _get_cached_sentiment(req.company_name)
        background_tasks.add_task(analyze_sentiment_background, req.company_name)
        if cached:
            cached["refreshing"] = True
            return cached
        return {
            "company_name": req.company_name,
            "has_data": False,
            "analyzing": True,
            "message": "正在重新分析，请稍后刷新…",
        }

    # 非强制：返回缓存（如有且未过期），否则后台刷新
    cached = _get_cached_sentiment(req.company_name)
    if cached and not cached.get("is_stale", False):
        return cached
    if cached:
        background_tasks.add_task(analyze_sentiment_background, req.company_name)
        cached["refreshing"] = True
        return cached
    background_tasks.add_task(analyze_sentiment_background, req.company_name)
    return {
        "company_name": req.company_name,
        "has_data": False,
        "analyzing": True,
        "message": "正在分析舆情数据，请稍后刷新…",
    }


@router.post("/analyze-all")
async def trigger_analysis_all():
    """触发所有监控企业的舆情分析。"""
    results = await asyncio.to_thread(analyze_all_sentiment)
    return {"analyzed": len(results), "results": results}


@router.get("/dashboard/overview")
async def sentiment_dashboard():
    """舆情总览看板。"""
    return await asyncio.to_thread(get_sentiment_dashboard)
