"""Execute one durable scheduled job behind permission, budget and audit gates."""

from __future__ import annotations

from typing import Any, Callable

from app.core.config import settings
from app.core.logging import get_logger
from app.db.postgres import get_cursor
from app.domains.scheduling import repo
from app.domains.scheduling.errors import ExecutionDenied
from app.domains.scheduling.periods import POLICY_GATED_KINDS, ScheduleDefinition

logger = get_logger(__name__)


def _actor_context(actor_id: str) -> tuple[bool, str]:
    with get_cursor() as (_, cur):
        cur.execute("SELECT is_active, role FROM users WHERE id=%s::uuid", (actor_id,))
        row = cur.fetchone()
    return (bool(row[0]), str(row[1])) if row else (False, "")


def _target_for(definition: ScheduleDefinition) -> dict[str, Any] | None:
    from app.domains.alert.service import get_watchlist_targets

    if definition.actor_kind == "user":
        active, role = _actor_context(definition.actor_id)
        if not active:
            raise ExecutionDenied("actor_inactive")
        targets = get_watchlist_targets(definition.actor_id, role)
    else:
        allowed = {
            item.strip()
            for item in settings.SCHEDULER_SERVICE_PRINCIPALS.split(",")
            if item.strip()
        }
        if not allowed or definition.actor_id not in allowed:
            raise ExecutionDenied("service_principal_not_allowed")
        targets = get_watchlist_targets()
    for target in targets:
        if str(target.get("monitor_target_id") or "") == definition.target_id:
            if definition.scope.get("company_id") and str(
                target.get("company_id") or ""
            ) != str(definition.scope["company_id"]):
                continue
            return target
    raise ExecutionDenied("target_revoked")


def _financial_check(target: dict[str, Any], _definition: ScheduleDefinition) -> dict[str, Any]:
    from app.services.scheduler import run_financial_check_for_target

    return run_financial_check_for_target(target)


def _full_refresh(target: dict[str, Any], definition: ScheduleDefinition) -> dict[str, Any]:
    from app.services.scheduler import run_refresh_for_target

    return run_refresh_for_target(target, definition.actor_id)


HANDLERS: dict[str, Callable[[dict[str, Any], ScheduleDefinition], dict[str, Any]]] = {
    "financial_check": _financial_check,
    "full_refresh": _full_refresh,
}
EXECUTABLE_KINDS = frozenset(HANDLERS)


def _reserve_budget(definition: ScheduleDefinition, job: dict[str, Any]) -> bool | None:
    if definition.kind not in POLICY_GATED_KINDS:
        return None
    if not settings.SCHEDULER_PAID_EXECUTION_ENABLED:
        raise ExecutionDenied("paid_execution_disabled")
    from app.core.paid_quota import (
        PaidQuotaExceeded,
        PaidQuotaUnavailable,
        reserve_paid_refresh_once,
    )

    try:
        return reserve_paid_refresh_once(
            definition.actor_id, f"scheduled:{job['id']}", 1
        )
    except PaidQuotaExceeded as exc:
        raise ExecutionDenied("paid_quota_exceeded") from exc
    except PaidQuotaUnavailable as exc:
        raise ExecutionDenied("paid_quota_unavailable") from exc


def execute_scheduled_job(job: dict[str, Any], worker_id: str) -> dict[str, Any]:
    """Run one claimed job; the caller owns the lease and heartbeat."""
    try:
        definition = ScheduleDefinition.from_payload(job["definition"])
    except Exception as exc:
        ok = repo.finish_scheduled_job(
            job, worker_id, "NEEDS_REVIEW", error=f"invalid_definition: {exc}"
        )
        return {"terminal": ok, "needs_review": True, "reason": "invalid_definition"}
    handler = HANDLERS.get(definition.kind)
    if handler is None:
        ok = repo.finish_scheduled_job(
            job, worker_id, "BLOCKED_POLICY", error="unsupported_kind"
        )
        return {"terminal": ok, "blocked": True, "reason": "unsupported_kind"}

    try:
        repo.assert_scheduled_lease(job, worker_id)
        target = _target_for(definition)
        budget_reserved = _reserve_budget(definition, job)
        repo.assert_scheduled_lease(job, worker_id)
        if definition.kind in POLICY_GATED_KINDS:
            repo.mark_provider_started(job, worker_id)
    except ExecutionDenied as exc:
        ok = repo.finish_scheduled_job(job, worker_id, "CANCELLED", error=str(exc))
        return {"terminal": ok, "denied": True, "reason": str(exc)}

    try:
        result = handler(target, definition)
    except Exception as exc:
        # A policy-gated provider may have received the request before failing.
        # Never retry that unknown outcome automatically.
        status = "NEEDS_REVIEW" if definition.kind in POLICY_GATED_KINDS else "FAILED"
        message = str(exc) or type(exc).__name__
        ok = repo.finish_scheduled_job(job, worker_id, status, error=message)
        logger.exception(
            "scheduled_execution_failed",
            job_id=job["id"], kind=definition.kind, status=status,
        )
        return {"terminal": ok, "failed": True, "status": status, "error": message}

    ok = repo.finish_scheduled_job(
        job,
        worker_id,
        "SUCCEEDED",
        result={"handler": definition.kind, "budget_reserved": budget_reserved, "outcome": result},
    )
    if not ok:
        # The provider already ran; the new owner must reconcile rather than
        # replay it. The persisted provider boundary drives that decision.
        return {
            "terminal": False, "needs_review": True,
            "reason": "lease_lost_after_success", "budget_reserved": budget_reserved,
        }
    return {"terminal": True, "succeeded": True, "budget_reserved": budget_reserved}


__all__ = [
    "EXECUTABLE_KINDS", "ExecutionDenied", "HANDLERS", "execute_scheduled_job",
]
