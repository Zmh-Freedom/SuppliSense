"""HTTP and SSE surface for durable sourcing-risk agent runs."""

import asyncio
import json
from typing import Annotated, Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_user
from app.domains.agent_run.service import (
    cancel_run,
    create_sourcing_risk_run,
    decide_action_proposal,
    get_sourcing_risk_run,
    stream_events,
    submit_clarification,
)
from app.domains.agent_run.schemas import (
    ApprovalDecisionRequest,
    CancelRunRequest,
    ClarificationRequest,
    CreateSourcingRiskRunRequest,
)
from app.schemas.user import UserInDB

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"], dependencies=[Depends(get_current_user)])


def _agent_run_sse_event(event: dict) -> str:
    return (
        f"id: {event['event_id']}\n"
        f"event: {event['event_type']}\n"
        f"data: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
    )


@router.post("", summary="创建寻源风险任务")
async def create_agent_run(data: CreateSourcingRiskRunRequest, current_user: UserInDB = Depends(get_current_user)):
    return await asyncio.to_thread(create_sourcing_risk_run, data, current_user.id, current_user.role.value)


@router.get("/{run_id}", summary="获取寻源风险任务")
async def get_agent_run(run_id: UUID, current_user: UserInDB = Depends(get_current_user)):
    return await asyncio.to_thread(get_sourcing_risk_run, str(run_id), current_user.id, current_user.role.value)


@router.get("/{run_id}/events", summary="订阅任务事件")
async def get_agent_run_events(
    run_id: UUID,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
    current_user: UserInDB = Depends(get_current_user),
):
    def event_generator() -> Iterator[str]:
        events = stream_events(str(run_id), last_event_id or 0, current_user.id, current_user.role.value)
        for event in events:
            if event["event_type"] == "keepalive":
                yield ": keepalive\n\n"
            else:
                yield _agent_run_sse_event(event)

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{run_id}/clarification", summary="提交澄清答案")
async def clarify_agent_run(run_id: UUID, data: ClarificationRequest, current_user: UserInDB = Depends(get_current_user)):
    return await asyncio.to_thread(submit_clarification, str(run_id), data, current_user.id, current_user.role.value)


@router.post("/{run_id}/approvals/{approval_id}", summary="提交审批决定")
async def approve_agent_run(run_id: UUID, approval_id: UUID, data: ApprovalDecisionRequest, current_user: UserInDB = Depends(get_current_user)):
    return await asyncio.to_thread(decide_action_proposal, str(run_id), str(approval_id), data, current_user.id, current_user.role.value)


@router.post("/{run_id}/cancel", summary="取消任务")
async def cancel_agent_run(run_id: UUID, data: CancelRunRequest, current_user: UserInDB = Depends(get_current_user)):
    return await asyncio.to_thread(cancel_run, str(run_id), data.expected_version, current_user.id, current_user.role.value)
