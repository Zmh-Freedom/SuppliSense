"""Authorized task-history API for the sourcing workspace."""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.domains.sourcing.workspace_schemas import SourcingTaskListResponse
from app.domains.sourcing.workspace_service import list_sourcing_tasks
from app.schemas.user import UserInDB

router = APIRouter(
    prefix="/sourcing-workspace",
    tags=["sourcing-workspace"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "/tasks",
    summary="读取我的寻源任务",
    description="聚合当前用户有权访问的智能寻源任务与兼容寻源请求，并使用稳定游标分页。",
    response_model=SourcingTaskListResponse,
)
async def get_sourcing_tasks(
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    scope: Annotated[Literal["mine", "all"], Query()] = "mine",
    current_user: UserInDB = Depends(get_current_user),
) -> SourcingTaskListResponse:
    result = await asyncio.to_thread(
        list_sourcing_tasks,
        current_user.id,
        current_user.role.value,
        cursor=cursor,
        limit=limit,
        scope=scope,
    )
    return SourcingTaskListResponse.model_validate(result)
