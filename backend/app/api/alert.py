from pydantic import BaseModel

from fastapi import APIRouter, Query, UploadFile

from app.db.mongo import get_db
from app.services.alert_service import (
    add_to_watchlist,
    detect_changes,
    get_latest_snapshot,
    get_watchlist,
    refresh_company,
    remove_from_watchlist,
)
from app.services.scheduler import run_financial_check, run_refresh_all

router = APIRouter()


class CompanyRequest(BaseModel):
    company_name: str


class BatchRequest(BaseModel):
    companies: list[str]


@router.get("/status")
async def alert_status(company_name: str = Query(..., description="企业名称")):
    snapshot = get_latest_snapshot(company_name)
    changes = detect_changes(company_name)
    return {
        "has_snapshot": snapshot is not None,
        "last_checked": snapshot["checked_at"].isoformat() if snapshot else None,
        "changes": changes,
    }


@router.post("/check")
async def alert_check(req: CompanyRequest):
    snapshot = get_latest_snapshot(req.company_name)
    if snapshot is None:
        return {
            "company_name": req.company_name,
            "changed": False,
            "message": "暂无历史快照，请先执行风险评估",
        }
    return detect_changes(req.company_name)


@router.get("/dashboard")
async def alert_dashboard():
    db = get_db()
    companies = [doc["company_name"] for doc in db["watchlist"].find()]

    distribution = {"低风险": 0, "中风险": 0, "高风险": 0, "未知": 0}
    details = []

    for name in companies:
        snap = db["alert_snapshots"].find_one(
            {"company_name": name}, sort=[("checked_at", -1)]
        )
        if snap:
            level = snap.get("risk_level", "未知")
            distribution[level] = distribution.get(level, 0) + 1
            details.append({
                "name": name,
                "score": snap.get("risk_score", 0),
                "level": level,
                "last_checked": snap["checked_at"].isoformat() if snap.get("checked_at") else None,
            })
        else:
            distribution["未知"] += 1
            details.append({"name": name, "score": None, "level": "未知", "last_checked": None})

    details.sort(key=lambda d: d["score"] if d["score"] is not None else -1, reverse=True)

    alert_count = db["alerts"].count_documents({})

    return {
        "total": len(companies),
        "distribution": distribution,
        "companies": details,
        "alert_count": alert_count,
    }


@router.get("/history")
async def alert_history(limit: int = Query(50, description="最大返回数")):
    db = get_db()
    docs = list(
        db["alerts"]
        .find({}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    for d in docs:
        if "created_at" in d:
            d["created_at"] = d["created_at"].isoformat()
    return {"count": len(docs), "alerts": docs}


@router.delete("/history")
async def clear_alerts():
    db = get_db()
    db["alerts"].delete_many({})
    return {"status": "cleared"}


@router.get("/watchlist")
async def list_watchlist():
    companies = get_watchlist()
    return {"count": len(companies), "companies": companies}


@router.post("/watch")
async def watch_company(req: CompanyRequest):
    return add_to_watchlist(req.company_name)


@router.post("/watch/batch")
async def watch_batch(req: BatchRequest):
    results = []
    for name in req.companies:
        r = add_to_watchlist(name.strip())
        results.append(r)
    return {"added": len(results), "results": results}


@router.post("/watch/upload")
async def watch_upload(file: UploadFile):
    import openpyxl

    wb = openpyxl.load_workbook(file.file, read_only=True)
    ws = wb.active

    names = []
    for row in ws.iter_rows(min_row=1, values_only=True):
        for cell in row:
            val = str(cell).strip() if cell else ""
            if val and len(val) > 2 and not val.startswith("#"):
                names.append(val)

    results = [add_to_watchlist(n) for n in names]
    return {"total": len(results), "results": results}


@router.post("/check-all")
async def check_all_watched():
    """Free: compare snapshots + AkShare financial for all watched companies."""
    return run_financial_check()


@router.post("/refresh")
async def refresh_alert(req: CompanyRequest):
    """Paid: refresh single company via Tianyancha API."""
    return refresh_company(req.company_name)


@router.post("/refresh-all")
async def refresh_all_watched():
    """Paid: refresh ALL watched companies via Tianyancha API."""
    return run_refresh_all()


@router.delete("/watch")
async def unwatch_company(company_name: str = Query(..., description="企业名称")):
    return remove_from_watchlist(company_name)
