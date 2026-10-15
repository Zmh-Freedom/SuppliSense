import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.core.deps import get_current_user
from app.core.logging import get_logger
from app.domains.auth.audit import AuditUnavailable, log_action
from app.schemas.user import UserInDB
from app.schemas import RiskCalculateRequest, RiskCalculateResponse, RiskAssessRequest
from app.domains.risk.service import assess_risk, calculate_risk

logger = get_logger(__name__)
router = APIRouter(dependencies=[Depends(get_current_user)])


async def _require_risk_audit(
    action: str, request: Request, user_id: str, company_name: str,
) -> None:
    try:
        await asyncio.to_thread(
            log_action, action=action, user_id=user_id,
            resource_type="company", resource_id=company_name,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"), required=True,
        )
    except AuditUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/rebuild-scores",
    summary="按最新评分体系重建全部供应商评分",
    description="管理员专用：按当前证据重算全部供应商评分；保留既有快照、告警和评估历史。",
)
async def rebuild_scores(http_request: Request, current_user=Depends(get_current_user)):
    if getattr(getattr(current_user, "role", None), "value", current_user.role) not in {"admin", "administrator"}:
        raise HTTPException(status_code=403, detail="仅管理员可重建全部供应商评分")
    await _require_risk_audit(
        "risk_score_rebuild_requested", http_request, current_user.id, "__all__"
    )
    from app.domains.risk.service import rebuild_all_supplier_scores

    return await asyncio.to_thread(rebuild_all_supplier_scores)


def _refresh_assessment(company_name: str) -> None:
    """Background task: re-assess risk and save snapshot."""
    try:
        from app.schemas import RiskAssessRequest
        assess_risk(RiskAssessRequest(company_name=company_name))
    except Exception as e:
        logger.warning("background_refresh_failed", company=company_name, error=str(e))

RISK_TIMEOUT_SECONDS = 120  # 风险评估超时时间（含 AkShare + 天眼查）


@router.post(
    "/calculate",
    response_model=RiskCalculateResponse,
    summary="计算企业风险评分",
    description="基于企业工商信息、司法风险、财务指标等多维度数据，计算 0-100 安全评分（分数越高风险越低）并返回风险等级。",
    responses={
        400: {"description": "请求参数错误"},
        504: {"description": "计算超时（>60秒）"},
        500: {"description": "服务器内部错误"},
    },
)
async def risk_calculate(request: RiskCalculateRequest):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(calculate_risk, request),
            timeout=RISK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"风险评估超时（>{RISK_TIMEOUT_SECONDS}秒），请稍后重试",
        )


@router.post(
    "/assess",
    response_model=RiskCalculateResponse,
    summary="快速评估企业风险（含缓存）",
    description="根据企业名称快速评估风险。返回 0-100 安全评分（分数越高风险越低）；优先返回缓存结果，2小时内数据直接返回，过期数据返回缓存同时后台刷新。",
    responses={
        400: {"description": "请求参数错误"},
        504: {"description": "评估超时（>60秒）"},
        500: {"description": "服务器内部错误"},
    },
)
async def risk_assess(
    request: RiskAssessRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    current_user: UserInDB = Depends(get_current_user),
):
    from datetime import datetime, timedelta, timezone
    from app.db.mongo import get_db

    db = get_db()
    recent = db["alert_snapshots"].find_one(
        {"company_name": request.company_name},
        sort=[("checked_at", -1)],
    )

    if recent and recent.get("checked_at"):
        checked = recent["checked_at"]
        if isinstance(checked, str):
            checked = datetime.fromisoformat(checked.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - checked.replace(tzinfo=timezone.utc) if checked.tzinfo is None else datetime.now(timezone.utc) - checked
        age_hours = round(age.total_seconds() / 3600, 1)

        is_listed = False
        base = db["baseinfo"].find_one({"name": request.company_name}, {"items.result.bondNum": 1, "items.result.bondName": 1})
        if base:
            r = base.get("items", {}).get("result", {})
            is_listed = bool(r.get("bondNum") or r.get("bondName"))

        resp = {
            "risk_score": recent.get("risk_score", 0),
            "risk_level": recent.get("risk_level", "未知"),
            "financial": recent.get("financial"),
            "risk_detail": recent.get("risk_detail"),
            "cached_at": checked.isoformat(),
            "cache_age_hours": age_hours,
            "is_stale": age_hours >= 2,
            "is_listed": is_listed,
        }

        # Fresh cache, no force: return immediately
        if age < timedelta(hours=2) and not request.force_refresh:
            return resp

        # Stale or force_refresh: return cached data, refresh in background
        if request.force_refresh or age >= timedelta(hours=2):
            await _require_risk_audit(
                "assess_risk_refresh_requested", http_request, current_user.id, request.company_name
            )
            background_tasks.add_task(_refresh_assessment, request.company_name)
            return resp

    # No cache at all — must compute synchronously
    await _require_risk_audit(
        "assess_risk_requested", http_request, current_user.id, request.company_name
    )
    try:
        fresh = await asyncio.wait_for(
            asyncio.to_thread(assess_risk, request),
            timeout=RISK_TIMEOUT_SECONDS,
        )
        fresh.cached_at = datetime.now(timezone.utc).isoformat()
        fresh.cache_age_hours = 0
        fresh.is_stale = False

        await asyncio.to_thread(
            log_action,
            action="assess_risk",
            user_id=current_user.id,
            resource_type="company",
            resource_id=request.company_name,
            details={"risk_score": fresh.risk_score, "risk_level": fresh.risk_level},
            ip_address=http_request.client.host if http_request.client else None,
            user_agent=http_request.headers.get("user-agent"),
        )

        return fresh
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"风险评估超时（>{RISK_TIMEOUT_SECONDS}秒），请稍后重试",
        )
