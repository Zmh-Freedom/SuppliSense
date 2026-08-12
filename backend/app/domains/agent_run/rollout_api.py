"""Administrator control surface for durable Agent Run V2 rollout state."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.deps import require_admin
from app.core.rollout_gate import promote_rollout, rollback_rollout
from app.schemas.user import UserInDB

router = APIRouter(prefix="/admin/agent-run-rollout", tags=["agent-run-rollout"])


class PromoteRolloutRequest(BaseModel):
    current_stage: str
    evidence: dict[str, Any]
    approval: dict[str, Any]


class RollbackRolloutRequest(BaseModel):
    stage: str
    reason: str = Field(min_length=2, max_length=500)
    in_flight: dict[str, int] = Field(default_factory=dict)


@router.post("/promote", summary="推进 Agent V2 灰度")
async def promote_agent_run_rollout(
    data: PromoteRolloutRequest, current_user: UserInDB = Depends(require_admin)
) -> dict[str, Any]:
    del current_user
    return await asyncio.to_thread(
        promote_rollout, settings, data.current_stage, data.evidence, approval=data.approval
    )


@router.post("/rollback", summary="冻结 Agent V2 灰度")
async def rollback_agent_run_rollout(
    data: RollbackRolloutRequest, current_user: UserInDB = Depends(require_admin)
) -> dict[str, Any]:
    return await asyncio.to_thread(
        rollback_rollout,
        settings,
        data.stage,
        reason=data.reason,
        in_flight=data.in_flight,
    )
