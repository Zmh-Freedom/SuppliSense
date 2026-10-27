"""Read-only procurement workbench API."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import get_current_user
from app.domains.alert import workbench_service
from app.domains.alert.workbench_schemas import ProcurementWorkbenchResponse
from app.schemas.user import UserInDB

router = APIRouter(prefix="/procurement-workbench", tags=["procurement-workbench"])


def _role(current_user: UserInDB) -> str:
    return current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)


@router.get(
    "",
    summary="查询采购工作台队列",
    response_model=ProcurementWorkbenchResponse,
)
async def get_procurement_workbench_endpoint(
    view: Literal["mine", "department"] = "mine",
    department_code: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_current_user),
) -> dict:
    try:
        return await asyncio.to_thread(
            workbench_service.get_procurement_workbench,
            current_user.id,
            _role(current_user),
            view=view,
            department_code=department_code,
            cursor=cursor,
            limit=limit,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
