import asyncio
from typing import Any, Literal

from bson import ObjectId

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Path, Query, UploadFile

from app.core.deps import get_current_user
from app.db.mongo import get_db
from app.domains.alert.rules import get_rules, set_rules
from app.domains.alert.review_tasks import (
    create_review_task,
    decide_review_task,
    execute_review_task,
    get_review_task,
    list_review_tasks,
)
from app.domains.alert.intake_service import (
    confirm_monitor_intake,
    create_monitor_intake,
    get_monitor_intake,
    select_monitor_intake_candidate,
)
from app.domains.risk.predictor import predict_all, predict_company
from app.schemas.user import UserInDB
from app.domains.alert.service import (
    add_to_watchlist,
    detect_changes,
    get_latest_snapshot,
    get_snapshot_history,
    get_watchlist_target_summaries,
    resolve_watchlist_identity,
    confirm_watchlist_identity,
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
    companies: list[str] = []
    targets: list[WatchRequest] = []


class ReviewTaskCreateRequest(BaseModel):
    monitor_target_id: str
    task_type: Literal["verify_identity", "assess", "review", "supplement_data", "continue_monitoring"]
    payload: dict[str, Any] = {}


class ReviewTaskDecisionRequest(BaseModel):
    expected_version: int
    decision: Literal["approved", "rejected"]
    comment: str | None = None


class ReviewTaskExecuteRequest(BaseModel):
    expected_version: int


class MonitorIdentityConfirmationRequest(BaseModel):
    company_id: str
    source_reference: str | None = None
    comment: str | None = None


class MonitorIntakeRequest(BaseModel):
    query: str


class MonitorIntakeSelectionRequest(BaseModel):
    candidate_id: str


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
            "review_task": target.get("review_task"),
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


@router.post(
    "/review-tasks",
    summary="创建采购复核任务",
    description="为监控对象创建持久化复核任务，任务必须经过审批后才能执行。",
)
async def create_monitor_review_task(
    req: ReviewTaskCreateRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        return await asyncio.to_thread(
            create_review_task,
            req.monitor_target_id,
            req.task_type,
            req.payload,
            current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/review-tasks",
    summary="查询采购复核任务",
    description="按监控对象查询当前用户可访问的复核任务及任务结果。",
)
async def list_monitor_review_tasks(
    monitor_target_id: str | None = Query(None, description="监控对象 ID"),
    current_user: UserInDB = Depends(get_current_user),
):
    return {
        "tasks": await asyncio.to_thread(
            list_review_tasks, monitor_target_id, current_user.id, current_user.role.value
        )
    }


@router.get(
    "/review-tasks/{task_id}",
    summary="查看采购复核任务详情",
    description="返回任务状态变更、审批、执行、证据和结果。",
)
async def get_monitor_review_task(
    task_id: str = Path(..., description="复核任务 ID"),
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        task = await asyncio.to_thread(
            get_review_task, task_id, current_user.id, current_user.role.value
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="复核任务不存在")
    return task


@router.post(
    "/review-tasks/{task_id}/decision",
    summary="审批采购复核任务",
    description="审批通过后任务进入可执行状态；拒绝会留下审批记录。",
)
async def decide_monitor_review_task(
    req: ReviewTaskDecisionRequest,
    task_id: str = Path(..., description="复核任务 ID"),
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        return await asyncio.to_thread(
            decide_review_task,
            task_id,
            req.expected_version,
            req.decision,
            req.comment,
            current_user.id,
            current_user.role.value,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/review-tasks/{task_id}/execute",
    summary="执行采购复核任务",
    description="仅执行已审批任务，并将证据和结果写回任务记录。",
)
async def execute_monitor_review_task(
    req: ReviewTaskExecuteRequest,
    task_id: str = Path(..., description="复核任务 ID"),
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        return await asyncio.to_thread(
            execute_review_task,
            task_id,
            req.expected_version,
            current_user.id,
            current_user.role.value,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    "/intakes",
    summary="发起供应商监控调查",
    description="先查询主体候选、内部交易、历史零件关系和可用外部资料；不会在此步骤写入监控清单。",
    responses={400: {"description": "查询线索无效"}},
)
async def create_monitor_intake_endpoint(
    req: MonitorIntakeRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        return await asyncio.to_thread(create_monitor_intake, req.query, str(current_user.id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/intakes/{intake_id}",
    summary="读取供应商监控调查",
    description="读取当前用户发起的调查结果及资料覆盖状态。",
    responses={404: {"description": "调查不存在"}},
)
async def get_monitor_intake_endpoint(
    intake_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    result = await asyncio.to_thread(get_monitor_intake, intake_id, str(current_user.id))
    if result is None:
        raise HTTPException(status_code=404, detail="调查不存在或无权访问")
    return result


@router.post(
    "/intakes/{intake_id}/selection",
    summary="选择监控调查主体",
    description="选择本次调查返回的候选主体后，按该主体重新关联交易和历史合作资料。",
    responses={400: {"description": "候选主体无效"}, 404: {"description": "调查不存在"}},
)
async def select_monitor_intake_candidate_endpoint(
    intake_id: str,
    req: MonitorIntakeSelectionRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        return await asyncio.to_thread(select_monitor_intake_candidate, intake_id, req.candidate_id, str(current_user.id))
    except ValueError as exc:
        status_code = 404 if "调查不存在" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post(
    "/intakes/{intake_id}/confirmation",
    summary="确认调查并加入监控",
    description="确认所选主体后创建稳定监控对象，并尽可能保存首次风险评估基线。",
    responses={400: {"description": "尚未选择主体"}, 404: {"description": "调查不存在"}},
)
async def confirm_monitor_intake_endpoint(
    intake_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        return await asyncio.to_thread(confirm_monitor_intake, intake_id, str(current_user.id))
    except ValueError as exc:
        status_code = 404 if "调查不存在" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get(
    "/watch/{monitor_target_id}/identity-candidates",
    summary="检索监控对象主体候选",
    description="复用寻源主体解析能力，根据监控对象名称返回企业主体候选；只检索，不自动绑定主体。",
    responses={404: {"description": "监控对象不存在"}},
)
async def monitor_identity_candidates(
    monitor_target_id: str,
    limit: int = Query(10, ge=1, le=20, description="最大候选数"),
):
    try:
        return await asyncio.to_thread(resolve_watchlist_identity, monitor_target_id, limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/watch/{monitor_target_id}/identity-confirmation",
    summary="确认并绑定监控对象主体",
    description="由采购人员选择已核验企业主体并绑定到监控对象，记录确认来源和操作人。",
    responses={
        400: {"description": "主体未核验或请求无效"},
        404: {"description": "监控对象或企业主体不存在"},
    },
)
async def confirm_monitor_identity(
    monitor_target_id: str,
    req: MonitorIdentityConfirmationRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        return await asyncio.to_thread(
            confirm_watchlist_identity,
            monitor_target_id,
            req.company_id,
            str(current_user.id),
            source_reference=req.source_reference,
            comment=req.comment,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message in {"监控对象不存在", "企业主体不存在"} else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


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
    summary="批量添加监控对象",
    description="优先接受带稳定身份的 targets；companies 仅作为旧名称批量接口兼容。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"},
    },
)
async def watch_batch(req: BatchRequest):
    if req.targets:
        results = [add_to_watchlist(
            target.company_name,
            target_type=target.target_type,
            monitor_target_id=target.monitor_target_id,
            supplier_id=target.supplier_id,
            candidate_id=target.candidate_id,
            company_id=target.company_id,
            supplier_code=target.supplier_code,
        ) for target in req.targets]
    else:
        results = [add_to_watchlist(name.strip()) for name in req.companies if name.strip()]
    return {"added": len(results), "results": results}


@router.post(
    "/watch/upload",
    summary="上传 Excel 批量导入监控对象",
    description="优先读取监控对象稳定身份字段；兼容只包含企业名称的旧模板，并将其标记为待核验企业主体。",
    responses={
        400: {"description": "文件格式错误或解析失败"},
        500: {"description": "服务器内部错误"},
    },
)
async def watch_upload(file: UploadFile):
    import openpyxl

    wb = openpyxl.load_workbook(file.file, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    def clean(value: object) -> str | None:
        text = str(value).strip() if value is not None else ""
        return text or None

    aliases = {
        "monitor_target_id": {"monitor_target_id", "监控对象id", "监控对象 ID"},
        "target_type": {"target_type", "对象类型", "监控对象类型"},
        "supplier_id": {"supplier_id", "供应商id", "供应商 ID"},
        "candidate_id": {"candidate_id", "候选id", "候选 ID"},
        "company_id": {"company_id", "企业id", "企业 ID", "主体id"},
        "supplier_code": {"supplier_code", "供应商代码"},
        "company_name": {"company_name", "供应商名称", "企业名称", "公司名称", "主体名称"},
    }
    header = [clean(value) or "" for value in (rows[0] if rows else ())]
    header_lookup = {value.lower(): index for index, value in enumerate(header) if value}
    field_indexes: dict[str, int] = {}
    for field, field_aliases in aliases.items():
        for alias in field_aliases:
            if alias.lower() in header_lookup:
                field_indexes[field] = header_lookup[alias.lower()]
                break

    results = []
    if field_indexes:
        for row in rows[1:]:
            if not any(clean(value) for value in row):
                continue
            payload = {
                field: clean(row[index]) if index < len(row) else None
                for field, index in field_indexes.items()
            }
            if not any(payload.get(field) for field in ("company_name", "supplier_id", "candidate_id", "company_id", "monitor_target_id")):
                continue
            results.append(add_to_watchlist(**payload))
    else:
        for row in rows:
            for cell in row:
                value = clean(cell)
                if value and len(value) > 2 and not value.startswith("#"):
                    results.append(add_to_watchlist(value))
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
