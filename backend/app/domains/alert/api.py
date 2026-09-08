import asyncio

from bson import ObjectId

from pydantic import BaseModel

from fastapi import APIRouter, Depends, Path, Query, UploadFile

from app.core.deps import get_current_user
from app.db.mongo import get_db
from app.domains.alert.rules import get_rules, set_rules
from app.domains.risk.predictor import predict_all, predict_company
from app.domains.alert.service import (
    add_to_watchlist,
    detect_changes,
    get_latest_snapshot,
    get_snapshot_history,
    get_watchlist_target_summaries,
    refresh_company,
    remove_from_watchlist,
)
from app.services.scheduler import run_financial_check, run_refresh_all

router = APIRouter(dependencies=[Depends(get_current_user)])


class CompanyRequest(BaseModel):
    company_name: str


class WatchRequest(BaseModel):
    company_name: str | None = None
    target_type: str | None = None
    monitor_target_id: str | None = None
    supplier_id: str | None = None
    candidate_id: str | None = None
    company_id: str | None = None
    supplier_code: str | None = None


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


@router.get(
    "/snapshots",
    summary="获取风险评估历史版本",
    description="按企业返回追加保存的风险评估快照，包含快照版本、评分体系和前一版本引用。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def alert_snapshots(
    company_name: str = Query(..., min_length=1, description="企业名称"),
    limit: int = Query(20, ge=1, le=100, description="最大返回数"),
):
    snapshots = get_snapshot_history(company_name.strip(), limit=limit)
    serialized = []
    for snapshot in snapshots:
        item = dict(snapshot)
        if "_id" in item:
            item["_id"] = str(item["_id"])
        for field in ("checked_at", "created_at"):
            if hasattr(item.get(field), "isoformat"):
                item[field] = item[field].isoformat()
        serialized.append(item)
    return {
        "company_name": company_name.strip(),
        "count": len(serialized),
        "snapshots": serialized,
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
    description="返回所有监控对象的风险分布、数据覆盖、变化状态和采购复核动作。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def alert_dashboard():
    db = get_db()
    targets = get_watchlist_target_summaries()
    companies = [target["company_name"] for target in targets if target.get("company_name")]

    distribution = {"低风险": 0, "中风险": 0, "高风险": 0, "未知": 0}
    details = []

    for target in targets:
        name = target.get("company_name", "")
        level = target.get("risk_level") or "未知"
        distribution[level] = distribution.get(level, 0) + 1
        risk_change = target.get("risk_change") or {}
        last_checked = target.get("last_checked_at")
        if hasattr(last_checked, "isoformat"):
            last_checked = last_checked.isoformat()
        details.append({
            "name": name,
            "monitor_target_id": target.get("monitor_target_id"),
            "target_type": target.get("target_type"),
            "identity_status": target.get("identity_status"),
            "supplier_id": target.get("supplier_id"),
            "candidate_id": target.get("candidate_id"),
            "company_id": target.get("company_id"),
            "supplier_code": target.get("supplier_code"),
            "score": target.get("risk_score"),
            "level": level,
            "risk_trend": risk_change.get("delta"),
            "risk_change": risk_change,
            "data_coverage": target.get("data_coverage"),
            "next_action": target.get("next_action"),
            "last_checked": last_checked,
        })

    details.sort(key=lambda d: d["score"] if d["score"] is not None else -1, reverse=True)

    alert_count = db["alerts"].count_documents({})

    return {
        "total": len(companies),
        "distribution": distribution,
        "companies": details,
        "targets": targets,
        "alert_count": alert_count,
    }


@router.get(
    "/history",
    summary="获取告警历史记录",
    description="按时间倒序返回告警历史记录列表。支持 unread_only 筛选。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def alert_history(
    limit: int = Query(50, description="最大返回数"),
    unread_only: bool = Query(False, description="仅返回未读告警"),
):
    db = get_db()
    filter_q = {"read": False} if unread_only else {}
    docs = list(
        db["alerts"]
        .find(filter_q)
        .sort("created_at", -1)
        .limit(limit)
    )
    unread_count = db["alerts"].count_documents({"read": False})
    alerts = []
    for d in docs:
        d["_id"] = str(d["_id"])
        if "created_at" in d:
            d["created_at"] = d["created_at"].isoformat()
        alerts.append(d)
    return {"count": len(alerts), "unread_count": unread_count, "alerts": alerts}


@router.put(
    "/history/{alert_id}/read",
    summary="标记单条告警已读",
    responses={
        404: {"description": "告警不存在"},
        500: {"description": "服务器内部错误"},
    },
)
async def mark_alert_read(alert_id: str = Path(..., description="告警 ID")):
    db = get_db()
    result = db["alerts"].update_one(
        {"_id": ObjectId(alert_id)},
        {"$set": {"read": True}},
    )
    if result.matched_count == 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="告警不存在")
    unread_count = db["alerts"].count_documents({"read": False})
    return {"status": "read", "unread_count": unread_count}


@router.put(
    "/history/read-all",
    summary="一键已读所有告警",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def mark_all_alerts_read():
    db = get_db()
    db["alerts"].update_many({"read": False}, {"$set": {"read": True}})
    return {"status": "all_read", "unread_count": 0}


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
    return {"status": "cleared", "unread_count": 0}


@router.get(
    "/watchlist",
    summary="获取监控对象工作台数据",
    description="返回当前所有监控对象及其身份、数据覆盖、风险变化和下一步采购动作；保留 companies 字段兼容旧客户端。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def list_watchlist():
    targets = get_watchlist_target_summaries()
    companies = [target["company_name"] for target in targets if target.get("company_name")]
    return {"count": len(companies), "companies": companies, "targets": targets}


@router.post(
    "/watch",
    summary="添加监控对象",
    description="将指定供应商、寻源候选或企业主体添加到风险监控，系统将定期检查其风险变化。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def watch_company(req: WatchRequest):
    return add_to_watchlist(
        req.company_name,
        target_type=req.target_type,
        monitor_target_id=req.monitor_target_id,
        supplier_id=req.supplier_id,
        candidate_id=req.candidate_id,
        company_id=req.company_id,
        supplier_code=req.supplier_code,
    )


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
async def unwatch_company(
    company_name: str | None = Query(None, description="企业名称（兼容参数）"),
    monitor_target_id: str | None = Query(None, description="监控对象 ID"),
    supplier_id: str | None = Query(None, description="正式供应商 ID"),
    candidate_id: str | None = Query(None, description="外部候选 ID"),
    company_id: str | None = Query(None, description="企业主体 ID"),
):
    return remove_from_watchlist(
        company_name,
        monitor_target_id=monitor_target_id,
        supplier_id=supplier_id,
        candidate_id=candidate_id,
        company_id=company_id,
    )


@router.get(
    "/tianyancha-stats",
    summary="天眼查 API 调用统计",
    description="查询天眼查 API 累计调用次数、按接口/企业的分布。",
)
async def tianyancha_stats():
    from app.services.tianyancha_client import get_api_stats
    return get_api_stats()
