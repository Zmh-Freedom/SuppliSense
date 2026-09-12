import asyncio

from pydantic import BaseModel

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from app.core.deps import get_current_user
from app.domains.risk.sentiment import (
    analyze_all_sentiment,
    analyze_sentiment,
    analyze_sentiment_background,
    get_sentiment_dashboard,
    get_sentiment_trend,
)

router = APIRouter(dependencies=[Depends(get_current_user)])


class AnalyzeRequest(BaseModel):
    company_name: str
    force_refresh: bool = False


@router.get(
    "/{company_name}",
    summary="获取企业舆情分析结果",
    description="获取指定企业的舆情情感分析结果。有缓存时立即返回（过期数据后台异步刷新），无缓存时返回分析中状态并后台启动分析。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def company_sentiment(company_name: str, background_tasks: BackgroundTasks):
    """获取企业舆情分析结果。
    - 有缓存：立即返回（即使已过期），后台异步刷新
    - 无缓存：立即返回 analyzing 状态，后台异步分析
    """
    from app.domains.risk.sentiment import (
        _analyzing_locks,
        _cached_sentiment_is_relevant,
        _get_cached_sentiment,
    )

    cached = _get_cached_sentiment(company_name)
    if cached and not _cached_sentiment_is_relevant(company_name, cached):
        # 过滤规则上线前写入的旧结果不再直接展示，交给后台重新采集。
        cached = None

    if cached:
        # 有缓存数据，立即返回；如果已过期则后台刷新
        if cached.get("is_stale", False):
            background_tasks.add_task(analyze_sentiment_background, company_name)
        if cached.get("is_stale", False) or company_name in _analyzing_locks:
            cached["analyzing"] = True
        return cached

    # 无缓存，启动后台分析，立即返回"分析中"状态
    background_tasks.add_task(analyze_sentiment_background, company_name)
    return {
        "company_name": company_name,
        "has_data": False,
        "analyzing": True,
        "message": "正在分析舆情数据，请稍后刷新…",
    }


@router.get(
    "/{company_name}/trend",
    summary="获取企业舆情趋势",
    description="获取指定企业最近 7 次舆情分析的趋势数据，包括情感得分和关键词变化。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def sentiment_trend(company_name: str):
    """获取企业舆情趋势（最近 7 次）。"""
    return await asyncio.to_thread(get_sentiment_trend, company_name)


@router.post(
    "/analyze",
    summary="触发企业舆情分析",
    description="手动触发指定企业的舆情分析。可选 force_refresh=true 强制重新分析（后台执行），否则优先返回缓存。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def trigger_analysis(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    """触发企业舆情分析。
    - force_refresh=false: 返回缓存（如有），后台刷新过期数据
    - force_refresh=true: 立即返回缓存，后台强制刷新
    """
    from app.domains.risk.sentiment import _cached_sentiment_is_relevant, _get_cached_sentiment

    if req.force_refresh:
        # 强制刷新：返回现有缓存 + 后台强制分析
        cached = _get_cached_sentiment(req.company_name)
        if cached and not _cached_sentiment_is_relevant(req.company_name, cached):
            cached = None
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
    if cached and not _cached_sentiment_is_relevant(req.company_name, cached):
        cached = None
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


@router.post(
    "/analyze-all",
    summary="批量触发所有监控企业舆情分析",
    description="对预警监控列表中的所有企业执行舆情分析，返回每个企业的分析结果。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def trigger_analysis_all():
    """触发所有监控企业的舆情分析。"""
    results = await asyncio.to_thread(analyze_all_sentiment)
    return {"analyzed": len(results), "results": results}


@router.get(
    "/dashboard/overview",
    summary="舆情总览看板",
    description="返回所有监控企业的舆情总览数据，包括整体情感分布、关键词云和风险企业列表。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def sentiment_dashboard(current_user=Depends(get_current_user)):
    """舆情总览看板。"""
    return await asyncio.to_thread(
        get_sentiment_dashboard,
        current_user.id,
        current_user.role.value,
    )
