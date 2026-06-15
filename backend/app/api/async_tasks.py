"""
Async task API routes.
"""

from pydantic import BaseModel
from fastapi import APIRouter, Depends

from app.core.celery_app import celery_app
from app.core.deps import require_admin_or_analyst
from app.schemas.user import UserInDB
from app.tasks.risk_tasks import (
    assess_risk_async,
    batch_refresh_all,
    check_all_async,
    refresh_company_async,
)
from app.tasks.sentiment_tasks import analyze_all_sentiment_async, analyze_sentiment_async

router = APIRouter(prefix="/async", tags=["async"])


class CompanyRequest(BaseModel):
    company_name: str


@router.post(
    "/assess",
    summary="提交异步风险评估任务",
    description="异步提交企业风险评估任务到 Celery 队列，返回任务 ID 以供后续查询。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def assess_async(
    req: CompanyRequest,
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    """Submit async risk assessment task."""
    task = assess_risk_async.delay(req.company_name)
    return {"task_id": task.id, "status": "submitted", "company_name": req.company_name}


@router.post(
    "/refresh",
    summary="提交异步天眼查刷新任务",
    description="异步提交天眼查数据刷新任务（付费功能）。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def refresh_async(
    req: CompanyRequest,
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    """Submit async Tianyancha refresh task (paid)."""
    task = refresh_company_async.delay(req.company_name)
    return {"task_id": task.id, "status": "submitted", "company_name": req.company_name}


@router.post(
    "/refresh-all",
    summary="提交批量天眼查刷新任务",
    description="异步批量提交所有监控企业的天眼查数据刷新任务（付费功能）。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def refresh_all_async(
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    """Submit batch refresh task for all watched companies (paid)."""
    task = batch_refresh_all.delay()
    return {"task_id": task.id, "status": "submitted"}


@router.post(
    "/check-all",
    summary="提交批量财务检查任务",
    description="异步提交所有监控企业的财务数据检查任务（免费功能）。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def check_all_async_endpoint(
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    """Submit financial check task for all watched companies (free)."""
    task = check_all_async.delay()
    return {"task_id": task.id, "status": "submitted"}


@router.post(
    "/sentiment",
    summary="提交异步舆情分析任务",
    description="异步提交指定企业的舆情分析任务到 Celery 队列。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def sentiment_async(
    req: CompanyRequest,
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    """Submit async sentiment analysis task."""
    task = analyze_sentiment_async.delay(req.company_name)
    return {"task_id": task.id, "status": "submitted", "company_name": req.company_name}


@router.post(
    "/sentiment-all",
    summary="提交批量舆情分析任务",
    description="异步提交所有监控企业的舆情分析任务到 Celery 队列。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def sentiment_all_async(
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    """Submit batch sentiment analysis task."""
    task = analyze_all_sentiment_async.delay()
    return {"task_id": task.id, "status": "submitted"}


@router.get(
    "/task/{task_id}",
    summary="查询异步任务状态",
    description="根据任务 ID 查询 Celery 异步任务的执行状态和结果。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
async def get_task_status(task_id: str):
    """Get task status and result."""
    result = celery_app.AsyncResult(task_id)

    response = {
        "task_id": task_id,
        "status": result.status,
    }

    if result.status == "PROGRESS":
        response["progress"] = result.info
    elif result.status == "SUCCESS":
        response["result"] = result.result
    elif result.status == "FAILURE":
        response["error"] = str(result.result)

    return response


@router.get(
    "/tasks",
    summary="列出活跃任务",
    description="查询当前 Celery worker 上正在执行的任务列表。需管理员或分析师权限。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员或分析师权限"},
        500: {"description": "服务器内部错误"},
    },
)
async def list_active_tasks(
    current_user: UserInDB = Depends(require_admin_or_analyst),
):
    """List active tasks (requires Flower or custom tracking)."""
    # Note: This is a simplified version. For full task tracking, use Flower.
    inspect = celery_app.control.inspect()
    active = inspect.active()

    tasks = []
    if active:
        for worker, worker_tasks in active.items():
            for task in worker_tasks:
                tasks.append({
                    "task_id": task["id"],
                    "name": task["name"],
                    "args": task.get("args", []),
                    "kwargs": task.get("kwargs", {}),
                })

    return {"active_tasks": tasks, "count": len(tasks)}
