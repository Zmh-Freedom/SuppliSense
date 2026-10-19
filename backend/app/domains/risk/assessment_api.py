"""Versioned, authenticated inspection of immutable assessment records."""

import asyncio
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.domains.risk import assessment_repo as repo
from app.domains.risk.assessment_policy import DIMENSIONS
from app.domains.risk.assessment_service import assess_company, resolve_identity

router = APIRouter(prefix="/api/v2/assessments", tags=["评估记录"])


class AssessmentRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    company_id: UUID
    monitor_target_id: str | None = None
    dimensions: list[Literal["financial", "judicial", "operational", "soft"]] = Field(
        default_factory=lambda: list(DIMENSIONS), min_length=1
    )
    purchase_scope: str = Field(default="", max_length=255)
    request_key: str = Field(min_length=1, max_length=255)
    linked_assessment_id: UUID | None = None


class ReviewRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=4000)
    decision: Literal["confirmed", "needs_review", "rejected"]
    request_key: str = Field(min_length=1, max_length=255)


def _role(user) -> str:
    return str(getattr(user.role, "value", user.role))


def _authorized(assessment_id: str, user) -> dict:
    row = repo.get_assessment(assessment_id, user.id, allow_all=_role(user) == "admin")
    if not row:
        raise HTTPException(404, "评估不存在或无权访问")
    # Historical verified identity is immutable, access is checked against today's scope.
    if row.get("company_id"):
        resolve_identity(
            row["company_name"],
            company_id=str(row["company_id"]),
            monitor_target_id=row.get("monitor_target_id"),
            actor_id=user.id,
            actor_role=_role(user),
            dimensions=row["identity"]["dimensions"],
            purchase_scope=row["identity"].get("purchase_scope", ""),
        )
    return row


def _public(row: dict) -> dict:
    # Collector permits and raw staging ownership never form part of the read contract.
    return {
        key: value
        for key, value in row.items()
        if key
        not in {
            "collector_token",
            "execution_fencing_token",
            "collection_lease_until",
            "acquired",
            "input_snapshot",
        }
    }


@router.post("", summary="创建或恢复同一评估")
async def create_assessment(request: AssessmentRequest, user=Depends(get_current_user)):
    linked = None
    if request.linked_assessment_id:
        linked = await asyncio.to_thread(
            _authorized, str(request.linked_assessment_id), user
        )
        if str(linked["company_id"]) != str(request.company_id):
            raise HTTPException(409, "关联评估必须属于同一企业主体")
    row = await asyncio.to_thread(
        assess_company,
        request.company_name,
        company_id=str(request.company_id),
        monitor_target_id=request.monitor_target_id,
        dimensions=request.dimensions,
        purchase_scope=request.purchase_scope,
        actor_id=user.id,
        actor_role=_role(user),
        request_key=request.request_key,
        linked_assessment_id=str(request.linked_assessment_id) if linked else None,
    )
    return _public(row)


@router.get("/company/{company_id}", summary="按企业查询评估与政策分段")
async def company_history(
    company_id: UUID,
    days: int = Query(90, ge=1, le=3650),
    user=Depends(get_current_user),
):
    await asyncio.to_thread(
        resolve_identity,
        "",
        company_id=str(company_id),
        actor_id=user.id,
        actor_role=_role(user),
        dimensions=list(DIMENSIONS),
    )
    rows = await asyncio.to_thread(
        repo.list_assessments, str(company_id), user.id, days=days
    )
    segments = {}
    for row in rows:
        try:
            await asyncio.to_thread(
                resolve_identity,
                row["company_name"],
                company_id=str(row["company_id"]),
                monitor_target_id=row.get("monitor_target_id"),
                actor_id=user.id,
                actor_role=_role(user),
                dimensions=row["identity"]["dimensions"],
                purchase_scope=row["identity"].get("purchase_scope", ""),
            )
        except Exception as exc:
            from app.core.errors import DomainError

            if isinstance(exc, DomainError) and exc.status_code == 403:
                continue
            raise
        key = (row["scope_key"], row["policy_hash"])
        segment = segments.setdefault(
            key,
            {
                "scope_key": key[0],
                "policy_hash": key[1],
                "policy": row["policy_snapshot"],
                "identity": row["identity"],
                "assessments": [],
            },
        )
        segment["assessments"].append(_public(row))
    return {"company_id": str(company_id), "segments": list(segments.values())}


@router.get("/{assessment_id}", summary="查询评估身份、证据、政策及复核")
async def assessment_detail(assessment_id: UUID, user=Depends(get_current_user)):
    row = await asyncio.to_thread(_authorized, str(assessment_id), user)
    reviews = await asyncio.to_thread(repo.get_reviews, str(assessment_id))
    return {**_public(row), "reviews": reviews}


@router.post("/{assessment_id}/reviews", summary="追加复核，保留原始评分")
async def review_assessment(
    assessment_id: UUID, request: ReviewRequest, user=Depends(get_current_user)
):
    row = await asyncio.to_thread(_authorized, str(assessment_id), user)
    return await asyncio.to_thread(
        repo.append_review,
        row["id"],
        user.id,
        reason=request.reason,
        decision=request.decision,
        request_key=request.request_key,
        allow_all=_role(user) == "admin",
    )
