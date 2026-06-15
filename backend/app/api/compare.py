"""
Compare API routes -- side-by-side company comparison.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.db.mongo import get_db

router = APIRouter(prefix="/compare", tags=["compare"])


class CompareRequest(BaseModel):
    company_names: list[str]


@router.post(
    "",
    summary="企业横向对比",
    description="对比多个企业的风险评分、财务指标、ESG 评级和舆情情感等维度。",
    responses={400: {"description": "请求参数错误"}, 500: {"description": "服务器内部错误"}},
)
async def compare_companies(req: CompareRequest):
    """Compare multiple companies side-by-side."""
    db = get_db()
    collection_names = db.list_collection_names()
    results = []

    for name in req.company_names:
        snap = db["alert_snapshots"].find_one(
            {"company_name": name}, sort=[("checked_at", -1)]
        )
        esg = (
            db["esg_results"].find_one(
                {"company_name": name}, sort=[("assessed_at", -1)]
            )
            if "esg_results" in collection_names
            else None
        )
        sent = (
            db["sentiment_results"].find_one(
                {"company_name": name}, sort=[("analyzed_at", -1)]
            )
            if "sentiment_results" in collection_names
            else None
        )

        item: dict = {
            "company_name": name,
            "risk_score": snap.get("risk_score") if snap else None,
            "risk_level": snap.get("risk_level") if snap else None,
            "financial": snap.get("financial") if snap else None,
        }
        if esg:
            item["esg"] = {
                "total_score": esg.get("total_score"),
                "total_level": esg.get("total_level"),
            }
        if sent:
            item["sentiment"] = {
                "sentiment_score": sent.get("sentiment_score"),
                "articles_count": sent.get("articles_count"),
            }
        results.append(item)

    return {"companies": results}
