import asyncio

from pydantic import BaseModel

from fastapi import APIRouter, Query

from app.services.sentiment import (
    analyze_all_sentiment,
    analyze_sentiment,
    get_sentiment_dashboard,
    get_sentiment_trend,
)

router = APIRouter()


class AnalyzeRequest(BaseModel):
    company_name: str
    force_refresh: bool = False


@router.get("/{company_name}")
async def company_sentiment(company_name: str):
    """获取企业舆情分析结果（6 小时缓存）。"""
    result = await asyncio.to_thread(analyze_sentiment, company_name)
    if result is None:
        return {"company_name": company_name, "has_data": False, "message": "未找到数据"}
    return result


@router.get("/{company_name}/trend")
async def sentiment_trend(company_name: str):
    """获取企业舆情趋势（最近 7 次）。"""
    return await asyncio.to_thread(get_sentiment_trend, company_name)


@router.post("/analyze")
async def trigger_analysis(req: AnalyzeRequest):
    """触发企业舆情分析。"""
    result = await asyncio.to_thread(analyze_sentiment, req.company_name, req.force_refresh)
    if result is None:
        return {"company_name": req.company_name, "has_data": False}
    return result


@router.post("/analyze-all")
async def trigger_analysis_all():
    """触发所有监控企业的舆情分析。"""
    results = await asyncio.to_thread(analyze_all_sentiment)
    return {"analyzed": len(results), "results": results}


@router.get("/dashboard/overview")
async def sentiment_dashboard():
    """舆情总览看板。"""
    return await asyncio.to_thread(get_sentiment_dashboard)
