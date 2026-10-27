"""Procurement case API routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import ValidationError

from app.core.deps import get_current_user
from app.domains.alert import case_service
from app.domains.alert.case_schemas import (
    CaseCommandRequest,
    CaseCreateRequest,
    CaseReviewTaskRequest,
)
from app.domains.alert.case_repo import IdempotencyConflict
from app.schemas.user import UserInDB

router = APIRouter(prefix="/procurement-cases", tags=["procurement-cases"])


def _role(user: UserInDB) -> str:
    return user.role.value if hasattr(user.role, "value") else str(user.role)


def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, case_service.CaseNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    conflict_type = getattr(case_service, "CaseConflictError", None)
    if isinstance(exc, IdempotencyConflict) or (
        conflict_type is not None and isinstance(exc, conflict_type)
    ):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


@router.get("", summary="查询采购事项")
async def list_procurement_cases(
    status: str | None = None,
    case_type: str | None = None,
    monitor_target_id: str | None = None,
    owner_user_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
    current_user: UserInDB = Depends(get_current_user),
    view: str = "accessible",
) -> dict[str, Any]:
    filters = {
        key: value
        for key, value in {
            "status": status,
            "case_type": case_type,
            "monitor_target_id": monitor_target_id,
            "owner_user_id": owner_user_id,
            "view": view,
        }.items()
        if value is not None
    }
    try:
        return await asyncio.to_thread(
            case_service.list_cases,
            {**filters, "limit": limit, "cursor": cursor},
            current_user.id,
            _role(current_user),
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("", summary="创建采购事项", status_code=201)
async def create_procurement_case(
    request: CaseCreateRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    current_user: UserInDB = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            case_service.create_case,
            request.model_dump(),
            current_user.id,
            _role(current_user),
            idempotency_key,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.get("/{case_id}", summary="查询采购事项详情")
async def get_procurement_case(
    case_id: str,
    current_user: UserInDB = Depends(get_current_user),
) -> dict[str, Any]:
    detail = await asyncio.to_thread(
        case_service.get_case_detail, case_id, current_user.id, _role(current_user)
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="采购事项不存在")
    return detail


@router.post("/{case_id}/commands", summary="处理采购事项")
async def send_procurement_case_command(
    case_id: str,
    request: CaseCommandRequest | dict[str, Any],
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    current_user: UserInDB = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        command = (
            request.model_dump(exclude_unset=True)
            if isinstance(request, CaseCommandRequest)
            else CaseCommandRequest.model_validate(request).model_dump(exclude_unset=True)
        )
        return await asyncio.to_thread(
            case_service.handle_case_command,
            case_id,
            command,
            current_user.id,
            _role(current_user),
            idempotency_key,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/{case_id}/review-tasks", summary="创建并关联技术复核任务")
async def create_case_review_task(
    case_id: str,
    request: CaseReviewTaskRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    current_user: UserInDB = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            case_service.create_linked_review_task,
            case_id,
            request.model_dump(),
            current_user.id,
            _role(current_user),
            idempotency_key,
        )
    except Exception as exc:
        _raise_api_error(exc)
