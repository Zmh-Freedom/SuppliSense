"""Administrator APIs for transactional Outbox operations."""

import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.deps import require_admin
from app.domains.outbox.service import list_events, replay_event
from app.schemas.outbox import (
    OutboxEventListResponse,
    OutboxEventResponse,
    OutboxReplayInput,
    OutboxReplayResponse,
)
from app.schemas.user import UserInDB

router = APIRouter(prefix="/admin/outbox", tags=["outbox"])


@router.get(
    "/events",
    response_model=OutboxEventListResponse,
    summary="查看 Outbox 事件",
    description="管理员按待处理、失败或死信状态查看确定性排序的 Outbox 事件。",
    responses={401: {"description": "未认证"}, 403: {"description": "需要管理员权限"}},
)
async def list_outbox_events(
    status: Literal["pending", "failed", "dead_letter"] = "pending",
    limit: int = Query(default=50, ge=1, le=100),
    current_user: UserInDB = Depends(require_admin),
) -> OutboxEventListResponse:
    """List operator-visible unpublished events with a bounded result set."""
    del current_user
    events = await asyncio.to_thread(list_events, status, limit)
    return OutboxEventListResponse(
        events=[
            OutboxEventResponse.model_validate(event)
            for event in sorted(events, key=lambda event: (event["occurred_at"], event["event_id"]))
        ]
    )


@router.post(
    "/events/{event_id}/replay",
    response_model=OutboxReplayResponse,
    summary="回放失败或死信 Outbox 事件",
    description="管理员提供原因后，重新排队未发布的失败或死信事件。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "需要管理员权限"},
        404: {"description": "Outbox 事件不存在"},
        409: {"description": "事件不可回放"},
        422: {"description": "请求参数校验失败"},
    },
)
async def replay_outbox_event(
    event_id: UUID,
    data: OutboxReplayInput,
    current_user: UserInDB = Depends(require_admin),
) -> OutboxReplayResponse:
    """Requeue an eligible event and record the administrator's audit reason."""
    result = await asyncio.to_thread(
        replay_event,
        str(event_id),
        data.reason,
        current_user.id,
    )
    return OutboxReplayResponse.model_validate(result)
