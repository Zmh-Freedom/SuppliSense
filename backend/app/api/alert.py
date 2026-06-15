import asyncio

from pydantic import BaseModel

from fastapi import APIRouter, Query, UploadFile

from app.db.mongo import get_db
from app.services.alert_rules import get_rules, set_rules
from app.services.predictor import predict_all, predict_company
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


class RuleItem(BaseModel):
    field: str
    operator: str
    threshold: float = 0
    severity: str = "warning"


class RulesRequest(BaseModel):
    company_name: str | None = None
    rules: list[RuleItem]


class BatchRequest(BaseModel):
    companies: list[str]


@router.get(
    "/status",
    summary="查询企业预警状态",
    description="获取指定企业的最新风险快照和变更检测结果。包含上次检查时间、风险评分变化等信息。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def alert_status(company_name: str = Query(..., description="企业名称")):
    snapshot = get_latest_snapshot(company_name)
    changes = detect_changes(company_name)
    return {
        "has_snapshot": snapshot is not None,
        "last_checked": snapshot["checked_at"].isoformat() if snapshot else None,
        "changes": changes,
    }


@router.post(
    "/check",
    summary="执行企业风险变更检测",
    description="对比指定企业的最新快照与历史快照，检测风险评分和关键指标的变化，生成变更报告。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def alert_check(req: CompanyRequest):
    snapshot = get_latest_snapshot(req.company_name)
    if snapshot is None:
        return {
            "company_name": req.company_name,
            "changed": False,
            "message": "暂无历史快照，请先执行风险评估",
        }
    return await asyncio.to_thread(detect_changes, req.company_name)


@router.get(
    "/dashboard",
    summary="预警总览面板",
    description="返回所有监控企业的风险分布统计、评分排名和告警数量汇总。使用聚合查询一次性获取所有最新快照。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def alert_dashboard():
    db = get_db()
    companies = [doc["company_name"] for doc in db["watchlist"].find()]

    distribution = {"低风险": 0, "中风险": 0, "高风险": 0, "未知": 0}
    details = []

    # 使用聚合查询一次性获取所有企业的最新快照（替代 N+1 循环查询）
    snap_map: dict = {}
    if companies:
        pipeline = [
            {"$match": {"company_name": {"$in": companies}}},
            {"$sort": {"checked_at": -1}},
            {"$group": {
                "_id": "$company_name",
                "doc": {"$first": "$$ROOT"},
            }},
        ]
        for doc in db["alert_snapshots"].aggregate(pipeline):
            snap = doc["doc"]
            snap_map[snap["company_name"]] = snap

    for name in companies:
        snap = snap_map.get(name)
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


@router.get(
    "/history",
    summary="获取告警历史记录",
    description="按时间倒序返回告警历史记录列表。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
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


@router.delete(
    "/history",
    summary="清空告警历史",
    description="删除所有告警历史记录，不可恢复。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def clear_alerts():
    db = get_db()
    db["alerts"].delete_many({})
    return {"status": "cleared"}


@router.get(
    "/watchlist",
    summary="获取监控列表",
    description="返回当前所有被监控的企业名称列表。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def list_watchlist():
    companies = get_watchlist()
    return {"count": len(companies), "companies": companies}


@router.post(
    "/watch",
    summary="添加企业到监控列表",
    description="将指定企业添加到预警监控列表，系统将定期检查该企业的风险变化。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def watch_company(req: CompanyRequest):
    return add_to_watchlist(req.company_name)


@router.post(
    "/watch/batch",
    summary="批量添加企业到监控列表",
    description="一次性将多个企业添加到预警监控列表。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def watch_batch(req: BatchRequest):
    results = []
    for name in req.companies:
        r = add_to_watchlist(name.strip())
        results.append(r)
    return {"added": len(results), "results": results}


@router.post(
    "/watch/upload",
    summary="上传 Excel 批量导入监控企业",
    description="从上传的 Excel 文件中解析企业名称，批量添加到预警监控列表。",
    responses={
        400: {"description": "文件格式错误或解析失败"},
        500: {"description": "服务器内部错误"},
    },
)
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

    wb.close()
    results = [add_to_watchlist(n) for n in names]
    return {"total": len(results), "results": results}


@router.post(
    "/check-all",
    summary="检查所有监控企业（免费）",
    description="对比所有监控企业的历史快照并使用 AkShare 获取最新财务数据进行风险检测。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def check_all_watched():
    """Free: compare snapshots + AkShare financial for all watched companies."""
    return await asyncio.to_thread(run_financial_check)


@router.post(
    "/refresh",
    summary="刷新单个企业数据（付费）",
    description="通过天眼查 API 刷新指定企业的最新工商和风险数据。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def refresh_alert(req: CompanyRequest):
    """Paid: refresh single company via Tianyancha API."""
    return await asyncio.to_thread(refresh_company, req.company_name)


@router.post(
    "/refresh-all",
    summary="刷新所有监控企业数据（付费）",
    description="通过天眼查 API 批量刷新所有监控企业的最新工商和风险数据。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def refresh_all_watched():
    """Paid: refresh ALL watched companies via Tianyancha API."""
    return await asyncio.to_thread(run_refresh_all)


@router.get(
    "/rules",
    summary="获取预警规则",
    description="获取预警规则配置。可指定企业名称获取企业独有规则，不传则返回全局默认规则。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def list_rules(company_name: str = Query(None, description="企业名称，不传返回全局规则")):
    rules = get_rules(company_name)
    return {"company_name": company_name or "__global__", "rules": rules}


@router.put(
    "/rules",
    summary="更新预警规则",
    description="更新预警规则配置。可指定企业名称设置企业独有规则，不指定则更新全局规则。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def update_rules(req: RulesRequest):
    return set_rules(req.company_name, [r.model_dump() for r in req.rules])


@router.get(
    "/predict",
    summary="预测所有监控企业的风险概率",
    description="基于历史风险数据，使用预测模型估算所有监控企业的未来风险概率。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def predict_all_companies():
    return await asyncio.to_thread(predict_all)


@router.get(
    "/predict/{company_name}",
    summary="预测单个企业的风险概率",
    description="基于历史风险数据，使用预测模型估算指定企业 future 风险概率和标签。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def predict_one(company_name: str):
    result = await asyncio.to_thread(predict_company, company_name)
    if result is None:
        return {"company_name": company_name, "probability": "unknown", "label": "未找到"}
    return result


@router.delete(
    "/watch",
    summary="从监控列表移除企业",
    description="将指定企业从预警监控列表中移除，不再对其风险变化进行监控。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def unwatch_company(company_name: str = Query(..., description="企业名称")):
    return remove_from_watchlist(company_name)
