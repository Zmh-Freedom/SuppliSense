"""Authorized task-history API for the sourcing workspace."""

import asyncio
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import get_current_user
from app.core.errors import DomainError
from app.domains.sourcing_risk.handoff_schemas import (
    CandidateHandoffDetailResponse,
    CandidateHandoffRequest,
    CandidateHandoffResponse,
)
from app.domains.sourcing_risk.handoff_service import (
    create_candidate_handoff,
    get_candidate_handoff,
)
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


@router.post(
    "/handoffs",
    summary="创建寻源候选交接",
    response_model=CandidateHandoffResponse,
)
async def post_candidate_handoff(
    payload: CandidateHandoffRequest,
    request_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    current_user: UserInDB = Depends(get_current_user),
) -> CandidateHandoffResponse:
    from app.domains.agent_run.api import _require_v2_route

    _require_v2_route(current_user, allow_shadow=False)
    result = await asyncio.to_thread(
        create_candidate_handoff,
        payload.model_dump(mode="json"),
        current_user.id,
        current_user.role.value,
        request_key,
    )
    return CandidateHandoffResponse.model_validate(result)


@router.get(
    "/handoffs/{handoff_id}",
    summary="读取寻源候选交接",
    response_model=CandidateHandoffDetailResponse,
)
async def read_candidate_handoff(
    handoff_id: UUID,
    current_user: UserInDB = Depends(get_current_user),
) -> CandidateHandoffDetailResponse:
    result = await asyncio.to_thread(get_candidate_handoff, str(handoff_id), current_user.id)
    if result is None:
        raise DomainError("SOURCING_HANDOFF_NOT_FOUND", "交接不存在或无权访问", 404)
    return CandidateHandoffDetailResponse.model_validate(result)
