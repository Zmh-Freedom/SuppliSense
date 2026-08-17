"""HTTP and SSE surface for durable sourcing-risk agent runs."""

import asyncio
import json
from collections.abc import Coroutine
from typing import Annotated, Any, Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.core.config import agent_run_v2_route, settings
from app.core.errors import DomainError
from app.core.deps import get_current_user
from app.domains.agent_run.service import (
    cancel_run,
    create_sourcing_risk_run,
    decide_action_proposal,
    get_sourcing_risk_run,
    retry_sourcing_risk_raw_payload_compensations,
    stream_events,
    submit_clarification,
    submit_identity_resolution,
)
from app.domains.agent_run.schemas import (
    AgentRunResponse,
    ApprovalDecisionRequest,
    CancelRunRequest,
    ClarificationRequest,
    CreateSourcingRiskRunRequest,
    IdentityResolutionRequest,
)
from app.graphs.sourcing_risk_v2.runner import resume_sourcing_risk_graph, start_sourcing_risk_graph
from app.schemas.user import UserInDB

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"], dependencies=[Depends(get_current_user)])


def _agent_run_sse_event(event: dict) -> str:
    return (
        f"id: {event['event_id']}\n"
        f"event: {event['event_type']}\n"
        f"data: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
    )


async def _schedule_graph(coroutine: Coroutine[Any, Any, None]) -> None:
    """Detach durable graph execution so HTTP handlers remain responsive."""
    asyncio.create_task(coroutine)


def _require_v2_route(user: UserInDB, *, allow_shadow: bool = True) -> str:
    from app.core.rollout_gate import get_rollout_state_snapshot

    snapshot = get_rollout_state_snapshot()
    if snapshot is None:
        raise DomainError("AGENT_RUN_V2_CONTROL_UNAVAILABLE", "Agent V2 控制面不可用，已拒绝请求", 503)
    if snapshot["state"] == "rollback_frozen":
        raise DomainError("AGENT_RUN_V2_ROLLBACK_FROZEN", "Agent V2 已回滚冻结，禁止新建或恢复", 409)
    route = agent_run_v2_route(
        user.id,
        user.role.value,
        settings,
        rollout=snapshot["stage"],
        rollout_state=snapshot["state"],
    )
    if route == "legacy":
        if settings.AGENT_RUN_V2_ROLLOUT_STATE == "rollback_frozen":
            code = "AGENT_RUN_V2_ROLLBACK_FROZEN"
        else:
            code = "AGENT_RUN_V2_DISABLED" if not settings.AGENT_RUN_V2_ENABLED else "AGENT_RUN_V2_NOT_IN_ROLLOUT"
        raise DomainError(code, "当前用户未进入 Agent V2 灰度范围", 409)
    if route == "shadow" and not allow_shadow:
        raise DomainError("AGENT_RUN_V2_SHADOW_READ_ONLY", "Shadow 模式禁止执行领域写入", 409)
    return route


@router.post("", summary="创建寻源风险任务")
async def create_agent_run(data: CreateSourcingRiskRunRequest, current_user: UserInDB = Depends(get_current_user)):
    _require_v2_route(current_user, allow_shadow=False)
    run = await asyncio.to_thread(create_sourcing_risk_run, data, current_user.id, current_user.role.value)
    await _schedule_graph(start_sourcing_risk_graph(str(run["id"])))
    return run


@router.get("/{run_id}", summary="获取寻源风险任务", response_model=AgentRunResponse)
async def get_agent_run(run_id: UUID, current_user: UserInDB = Depends(get_current_user)) -> AgentRunResponse:
    _require_v2_route(current_user)
    detail = await asyncio.to_thread(get_sourcing_risk_run, str(run_id), current_user.id, current_user.role.value)
    return AgentRunResponse.model_validate({**detail, "run_id": detail.get("run_id") or detail["id"]})


@router.post("/{run_id}/raw-payload-compensations/retry", summary="重试原始证据补偿")
async def retry_agent_run_raw_payload_compensations(
    run_id: UUID, current_user: UserInDB = Depends(get_current_user)
) -> dict[str, list[dict[str, str]]]:
    _require_v2_route(current_user, allow_shadow=False)
    statuses = await asyncio.to_thread(
        retry_sourcing_risk_raw_payload_compensations,
        str(run_id),
        current_user.id,
        current_user.role.value,
    )
    return {"raw_payload_statuses": statuses}


@router.get("/{run_id}/events", summary="订阅任务事件")
async def get_agent_run_events(
    run_id: UUID,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
    current_user: UserInDB = Depends(get_current_user),
):
    _require_v2_route(current_user)
    await asyncio.to_thread(
        get_sourcing_risk_run,
        str(run_id),
        current_user.id,
        current_user.role.value,
    )

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
    _require_v2_route(current_user, allow_shadow=False)
    run = await asyncio.to_thread(submit_clarification, str(run_id), data, current_user.id, current_user.role.value)
    await _schedule_graph(
        resume_sourcing_risk_graph(str(run_id), {"requirement_input": dict(run.get("requirement") or {})})
    )
    return run


@router.post("/{run_id}/identity-resolution", summary="提交身份审核结果")
async def resolve_agent_run_identity(
    run_id: UUID,
    data: IdentityResolutionRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    _require_v2_route(current_user, allow_shadow=False)
    run = await asyncio.to_thread(submit_identity_resolution, str(run_id), data, current_user.id, current_user.role.value)
    await _schedule_graph(resume_sourcing_risk_graph(str(run_id), {"identity_resolutions": dict(data.resolutions)}))
    return run


@router.post("/{run_id}/approvals/{approval_id}/decisions", summary="提交审批决定")
@router.post("/{run_id}/approvals/{approval_id}", summary="提交审批决定（兼容旧路径）", deprecated=True)
async def approve_agent_run(run_id: UUID, approval_id: UUID, data: ApprovalDecisionRequest, current_user: UserInDB = Depends(get_current_user)):
    _require_v2_route(current_user, allow_shadow=False)
    return await asyncio.to_thread(decide_action_proposal, str(run_id), str(approval_id), data, current_user.id, current_user.role.value)


@router.post("/{run_id}/cancel", summary="取消任务")
async def cancel_agent_run(run_id: UUID, data: CancelRunRequest, current_user: UserInDB = Depends(get_current_user)):
    _require_v2_route(current_user, allow_shadow=False)
    return await asyncio.to_thread(cancel_run, str(run_id), data.expected_version, current_user.id, current_user.role.value)
