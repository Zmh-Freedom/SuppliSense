"""Shared assessment application boundary, independent of graphs and transport."""

from datetime import datetime
from uuid import uuid4

from app.core.errors import DomainError
from app.db.mongo import get_db
from app.db.postgres import get_cursor
from app.domains.company import service as company_service
from app.domains.risk import assessment_repo as repo
from app.domains.risk.assessment_contracts import (
    canonical_hash,
    identity_hash,
    get_assessment_context,
)
from app.domains.risk.assessment_evidence import collect_inputs
from app.domains.risk.assessment_policy import current_policy
from app.domains.risk.assessment_scoring import score_frozen_inputs
from app.domains.sourcing_risk.evidence_service import stage_raw_payloads
from app.schemas import RiskCalculateResponse


def _actor(actor_id: str | None, role: str | None) -> tuple[str, str]:
    context = get_assessment_context()
    actor_id = actor_id or context.get("actor_id") or "system:legacy"
    if actor_id.startswith("system:"):
        return actor_id, "system"
    with get_cursor() as (_, cur):
        cur.execute("SELECT role,is_active FROM users WHERE id=%s", (actor_id,))
        row = cur.fetchone()
    if not row or not row[1]:
        raise DomainError("ASSESSMENT_FORBIDDEN", "操作者不存在或已停用", 403)
    return actor_id, str(row[0])


def resolve_identity(
    company_name: str,
    *,
    company_id: str | None = None,
    monitor_target_id: str | None = None,
    actor_id: str,
    actor_role: str,
    dimensions: list[str],
    purchase_scope: str = "",
) -> dict:
    target = {}
    if monitor_target_id:
        from app.domains.alert.service import _find_watchlist_target, _can_access_target

        target = (
            _find_watchlist_target(
                monitor_target_id=monitor_target_id, backfill_id=False
            )
            or {}
        )
        if not target or (
            actor_role != "system"
            and not _can_access_target(target, actor_id, actor_role)
        ):
            raise DomainError("ASSESSMENT_FORBIDDEN", "无权访问该监控对象", 403)
        if (
            company_id
            and target.get("company_id")
            and company_id != target["company_id"]
        ):
            raise ValueError("监控对象与评估主体不一致")
        company_id = company_id or target.get("company_id")
    if company_id:
        company = company_service.get_company(company_id)
    else:
        resolution = company_service.search_identity(company_name)
        exact = resolution.get("exact") or {}
        company = (
            company_service.get_company(exact["company_id"])
            if resolution.get("resolution") == "exact"
            else None
        )
    if not company or company.get("verification_status") != "verified":
        raise DomainError(
            "ASSESSMENT_IDENTITY_REQUIRED",
            "企业主体未唯一核验，请先确认 company_id",
            409,
        )
    if actor_role not in {"admin", "system"}:
        from app.domains.supplier.access import (
            formal_supplier_id_by_name,
            can_access_supplier,
        )

        supplier = formal_supplier_id_by_name(company["legal_name"])
        if supplier and not can_access_supplier(supplier, actor_id, actor_role):
            raise DomainError("ASSESSMENT_FORBIDDEN", "无权访问该正式供应商", 403)
    return {
        "company_id": str(company["id"]),
        "company_name": company["legal_name"],
        "monitor_target_id": monitor_target_id,
        "dimensions": sorted(set(dimensions)),
        "purchase_scope": purchase_scope,
        "target_type": target.get("target_type"),
        "supplier_id": target.get("supplier_id"),
        "candidate_id": target.get("candidate_id"),
        "supplier_code": target.get("supplier_code"),
    }


def _collect_new_inputs(
    row: dict, *, refresh_sources: bool, include_soft: bool
) -> tuple[dict, list[dict], list[dict]]:
    from app.domains.risk import service

    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT COUNT(*) FROM companies WHERE legal_name=%s AND merged_into_id IS NULL",
            (row["company_name"],),
        )
        if cur.fetchone()[0] > 1:
            raise DomainError(
                "ASSESSMENT_SOURCE_IDENTITY_REQUIRED",
                "同名主体的名称缓存无法证明证据归属，请核对稳定来源主体",
                409,
            )
    profile = (
        service.get_company_profile(row["company_name"])
        if refresh_sources
        else service.get_baseinfo(row["company_name"])
    )
    if profile is None:
        raise ValueError("缺少企业基础资料")
    if refresh_sources and "judicial" in row["policy_snapshot"]["required_dimensions"]:
        from app.services.tianyancha_client import ensure_court_register_evidence

        ensure_court_register_evidence(row["company_name"])
    return collect_inputs(row, profile, include_soft=include_soft)


def verify_frozen_payloads(row: dict, manifest: list[dict]) -> list[dict]:
    raw = []
    for entry in manifest:
        reference = entry.get("raw_payload_ref")
        if not reference:
            continue
        payload = get_db()["agent_evidence_payloads"].find_one(
            {"raw_payload_ref": reference, "assessment_id": row["id"]}
        )
        if (
            not payload
            or payload.get("company_id") != str(row["company_id"])
            or payload.get("lifecycle_status") not in {"pending", "committed"}
            or payload.get("staging_owner") != str(row["collector_token"])
            or canonical_hash(payload.get("raw_payload")) != entry.get("content_hash")
        ):
            raise DomainError(
                "ASSESSMENT_EVIDENCE_UNVERIFIED",
                "冻结证据不可读或内容哈希不一致，需要核对",
                409,
            )
        raw.append(payload)
    return raw


def assess_company(
    company_name: str,
    *,
    company_id: str | None = None,
    monitor_target_id: str | None = None,
    actor_id: str | None = None,
    actor_role: str | None = None,
    request_key: str | None = None,
    dimensions: list[str] | None = None,
    purchase_scope: str | None = None,
    run_id: str | None = None,
    logical_key: str | None = None,
    linked_assessment_id: str | None = None,
    include_soft: bool = True,
    refresh_sources: bool = True,
) -> dict:
    from app.core.config import settings
    import os

    context = get_assessment_context()
    actor_id, actor_role = _actor(actor_id, actor_role)
    policy = current_policy(dimensions)
    monitor_target_id = monitor_target_id or context.get("monitor_target_id")
    identity = resolve_identity(
        company_name,
        company_id=company_id or context.get("company_id"),
        monitor_target_id=monitor_target_id,
        actor_id=actor_id,
        actor_role=actor_role,
        dimensions=policy["required_dimensions"],
        purchase_scope=purchase_scope or context.get("purchase_scope") or "",
    )
    run_id = run_id or context.get("run_id")
    if run_id:
        task = (
            logical_key
            or context.get("task_id")
            or context.get("logical_key")
            or "assess_risk"
        )
        logical_key = canonical_hash(
            {
                "task": task,
                "company_id": identity["company_id"],
                "scope": {
                    key: value
                    for key, value in identity.items()
                    if key != "company_name"
                },
            }
        )
    row = repo.begin_assessment(
        identity,
        actor_id=actor_id,
        run_id=run_id,
        logical_key=logical_key,
        request_key=request_key or context.get("request_key") or str(uuid4()),
        policy=policy,
        linked_assessment_id=linked_assessment_id,
        provenance={
            "source": context.get("source", "risk_service"),
            "run_id": run_id,
            "logical_task": context.get("task_id") or logical_key,
            "tool_version": context.get("tool_version"),
            "model": context.get("model") or settings.LLM_MODEL,
            "prompt_version": "soft-evidence-1",
            "deployment_version": os.getenv("GIT_COMMIT", "unknown"),
            "input_plan_hash": canonical_hash(
                {"task": context.get("task_id"), "logical_key": logical_key}
            ),
            "calculation_version": policy["calculation_version"],
        },
    )
    if row["assessment_status"] == "committed":
        return repo.get_assessment(row["id"], actor_id)
    if row["assessment_status"] == "collecting":
        if not row["acquired"]:
            raise DomainError(
                "ASSESSMENT_IN_PROGRESS",
                "相同评估正在采集，请按原 ID 查询或稍后重试",
                409,
                {"assessment_id": row["id"]},
            )
        try:
            inputs, manifest, payloads = _collect_new_inputs(
                row, refresh_sources=refresh_sources, include_soft=include_soft
            )
            repo.reserve_payloads(row["id"], str(row["collector_token"]), payloads)
            stage_raw_payloads(payloads, staging_owner=str(row["collector_token"]))
            verify_frozen_payloads(row, manifest)
            row = repo.freeze_assessment(
                row["id"], str(row["collector_token"]), inputs=inputs, manifest=manifest
            )
        except BaseException:
            repo.abort_collection(row["id"], str(row["collector_token"]))
            raise
    if row["assessment_status"] != "frozen":
        raise DomainError(
            "ASSESSMENT_NEEDS_REVIEW",
            "评估采集或原始证据需要核对，请创建关联的新评估",
            409,
            {"assessment_id": row["id"]},
        )
    detail = repo.get_assessment(row["id"], actor_id)
    manifest = detail["evidence_manifest"]
    verify_frozen_payloads(row, manifest)
    digest = canonical_hash({"inputs": row["input_snapshot"], "manifest": manifest})
    if digest != row["input_digest"]:
        raise DomainError("ASSESSMENT_INPUT_MISMATCH", "冻结输入摘要不一致", 409)
    result = score_frozen_inputs(
        row["input_snapshot"], manifest, row["policy_snapshot"]
    )
    repo.commit_assessment(
        row["id"], str(row["collector_token"]), result=result, quality=result["quality"]
    )
    return repo.get_assessment(row["id"], actor_id)


def legacy_response(row: dict) -> RiskCalculateResponse:
    if (
        not row.get("result")
        or row.get("quality") not in {"complete", "partial"}
        or row["result"].get("risk_score") is None
    ):
        raise DomainError(
            "ASSESSMENT_EVIDENCE_UNAVAILABLE", "证据不足，暂不能生成安全评分", 503
        )
    value = dict(row["result"])
    value.pop("quality", None)
    assessed = row["assessed_at"]
    value["cached_at"] = (
        assessed.isoformat() if isinstance(assessed, datetime) else str(assessed)
    )
    return RiskCalculateResponse.model_validate(value)


def cached_assessment(
    company_name: str, *, actor_id: str, actor_role: str
) -> dict | None:
    actor_id, actor_role = _actor(actor_id, actor_role)
    policy = current_policy()
    identity = resolve_identity(
        company_name,
        actor_id=actor_id,
        actor_role=actor_role,
        dimensions=policy["required_dimensions"],
    )
    return repo.find_cached(identity, actor_id, canonical_hash(policy))


def monitor_assessment(
    company_name: str,
    *,
    monitor_target_id: str | None = None,
    company_id: str | None = None,
    actor_id: str | None = None,
    request_key: str | None = None,
) -> RiskCalculateResponse:
    row = assess_company(
        company_name,
        company_id=company_id,
        monitor_target_id=monitor_target_id,
        actor_id=actor_id,
        request_key=request_key,
        include_soft=False,
        refresh_sources=False,
    )
    response = legacy_response(row)
    response.risk_detail = {
        **(response.risk_detail or {}),
        "assessment_id": row["id"],
        "policy_hash": row["policy_hash"],
    }
    return response


def comparable_trend(
    company_name: str, *, actor_id: str, actor_role: str, days: int = 90
) -> list[dict]:
    policy = current_policy()
    identity = resolve_identity(
        company_name,
        actor_id=actor_id,
        actor_role=actor_role,
        dimensions=policy["required_dimensions"],
    )
    rows = repo.list_assessments(identity["company_id"], actor_id, days=days)
    rows = [
        row
        for row in rows
        if row["request_hash"] == identity_hash(identity)
        and row["result"].get("risk_score") is not None
    ]
    if not rows:
        return []
    latest = rows[0]
    return [
        {
            "date": row["assessed_at"].strftime("%Y-%m-%d"),
            "risk_score": row["risk_score"],
            "risk_level": row["risk_level"],
        }
        for row in reversed(rows)
        if row["scope_key"] == latest["scope_key"]
        and row["policy_hash"] == latest["policy_hash"]
    ]


def legacy_snapshot_history(
    company_name: str,
    *,
    actor_id: str,
    actor_role: str,
    monitor_target_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    identity = resolve_identity(
        company_name,
        monitor_target_id=monitor_target_id,
        actor_id=actor_id,
        actor_role=actor_role,
        dimensions=list(current_policy()["required_dimensions"]),
    )
    rows = repo.list_assessments(identity["company_id"], actor_id, days=3650)
    snapshots = []
    for row in rows:
        if len(row["identity"]["dimensions"]) != 4 or row["quality"] not in {
            "complete",
            "partial",
        }:
            continue
        if monitor_target_id and row.get("monitor_target_id") != monitor_target_id:
            continue
        try:
            resolve_identity(
                row["company_name"],
                company_id=str(row["company_id"]),
                monitor_target_id=row.get("monitor_target_id"),
                actor_id=actor_id,
                actor_role=actor_role,
                dimensions=row["identity"]["dimensions"],
                purchase_scope=row["identity"].get("purchase_scope", ""),
            )
        except DomainError as exc:
            if exc.status_code == 403:
                continue
            raise
        snapshots.append(
            {
                **(row["result"] or {}),
                "_id": "assessment:" + row["id"],
                "snapshot_id": row["id"],
                "assessment_id": row["id"],
                "snapshot_version": row["assessment_version"],
                "previous_snapshot_id": row["previous_assessment_id"],
                "company_name": row["company_name"],
                "company_id": str(row["company_id"]),
                "target_type": row["identity"].get("target_type"),
                "supplier_id": row["identity"].get("supplier_id"),
                "supplier_code": row["identity"].get("supplier_code"),
                "candidate_id": row["identity"].get("candidate_id"),
                "monitor_target_id": row.get("monitor_target_id"),
                "actor_id": row["actor_id"],
                "assessment_scope_key": row["scope_key"],
                "policy_hash": row["policy_hash"],
                "scoring_version": row["scoring_version"],
                "checked_at": row["assessed_at"],
                "quality": row["quality"],
            }
        )
    return snapshots[: max(1, min(limit, 100))]
