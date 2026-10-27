"""Plan and apply watchlist targets to immutable durable schedules."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.db.postgres import get_cursor
from app.domains.scheduling import repo
from app.domains.scheduling.periods import ScheduleDefinition

KIND_PREFIX = {
    "financial_check": "watchlist-financial",
    "full_refresh": "watchlist-refresh",
}
KIND_CRON_ENV = {
    "financial_check": ("ALERT_CHECK_CRON", "0 9 * * *"),
    "full_refresh": ("ALERT_REFRESH_CRON", "0 9 * * 1"),
}


def _active_users(owner_ids: set[str]) -> dict[str, str]:
    if not owner_ids:
        return {}
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT id::text,role FROM users WHERE is_active AND id::text = ANY(%s)",
            (sorted(owner_ids),),
        )
        return {str(row[0]): str(row[1]) for row in cur.fetchall()}


def _valid_uuid(value: Any) -> bool:
    try:
        UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _latest_by_target(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["schedule_id"]), str(row["target_id"]))
        if key not in latest or int(row["revision"]) > int(latest[key]["revision"]):
            latest[key] = row
    return latest


def _same_definition(row: dict[str, Any], definition: ScheduleDefinition) -> bool:
    return all(
        [
            str(row["cron"]) == definition.cron,
            str(row["timezone"]) == definition.timezone,
            str(row["actor_kind"]) == definition.actor_kind,
            str(row["actor_id"]) == definition.actor_id,
            str(row["kind"]) == definition.kind,
            dict(row.get("scope") or {}) == definition.scope,
        ]
    )


def build_watchlist_migration(
    *,
    service_principal: str | None = None,
    include_paid: bool = False,
    timezone: str = "Asia/Shanghai",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Map watchlist targets to actors without writing anything.

    A target owner is used only when the account is active and the same
    permission function used at execution time can see the target. Otherwise
    an explicitly configured service principal is used; with neither, the
    target is reported and skipped.
    """
    from app.domains.alert.service import get_watchlist_targets

    now = now or datetime.now(UTC)
    configured = {
        item.strip()
        for item in settings.SCHEDULER_SERVICE_PRINCIPALS.split(",")
        if item.strip()
    }
    service_principal = service_principal or (sorted(configured)[0] if configured else None)
    if service_principal and service_principal not in configured:
        raise ValueError(
            "service principal 未在 SCHEDULER_SERVICE_PRINCIPALS 中配置，"
            "迁移后会被执行门禁拒绝"
        )
    targets = get_watchlist_targets()
    owner_ids = {
        str(target.get("owner_user_id"))
        for target in targets
        if _valid_uuid(target.get("owner_user_id"))
    }
    active_roles = _active_users(owner_ids)
    accessible: dict[str, set[str]] = {}
    for owner_id in owner_ids:
        role = active_roles.get(owner_id)
        if role is None:
            continue
        accessible[owner_id] = {
            str(target.get("monitor_target_id"))
            for target in get_watchlist_targets(owner_id, role)
        }

    existing = _latest_by_target(repo.list_schedules())
    kinds = ["financial_check"] + (["full_refresh"] if include_paid else [])
    entries: list[dict[str, Any]] = []
    for target in sorted(targets, key=lambda item: str(item.get("monitor_target_id") or "")):
        target_id = str(target.get("monitor_target_id") or "")
        if not target_id:
            entries.append({"target_id": "", "action": "skip", "reason": "missing_target_id"})
            continue
        owner_id = str(target.get("owner_user_id") or "")
        if owner_id in active_roles and target_id in accessible.get(owner_id, set()):
            actor_kind, actor_id = "user", owner_id
            mapping = "target_owner"
        elif service_principal:
            actor_kind, actor_id = "service", service_principal
            if not owner_id:
                mapping = "service_principal_no_owner"
            elif owner_id not in active_roles:
                mapping = "service_principal_owner_inactive"
            else:
                mapping = "service_principal_owner_access_denied"
        else:
            entries.append({
                "target_id": target_id,
                "owner_user_id": owner_id or None,
                "action": "skip",
                "reason": "no_eligible_owner_or_service_principal",
            })
            continue

        scope = {"target_id": target_id}
        if target.get("supplier_id"):
            scope["supplier_id"] = str(target["supplier_id"])
        if target.get("company_id"):
            scope["company_id"] = str(target["company_id"])

        for kind in kinds:
            prefix = KIND_PREFIX[kind]
            env_name, default_cron = KIND_CRON_ENV[kind]
            schedule_id = f"{prefix}:{target_id}"
            definition = ScheduleDefinition(
                schedule_id=schedule_id,
                revision=1,
                target_id=target_id,
                cron=os.getenv(env_name, default_cron),
                timezone=timezone,
                actor_kind=actor_kind,
                actor_id=actor_id,
                kind=kind,
                scope=scope,
                effective_at=now,
            )
            previous = existing.get((schedule_id, target_id))
            if previous is not None and _same_definition(previous, definition):
                action, revision = "noop", int(previous["revision"])
                payload = None
            else:
                action = "create" if previous is None else "update"
                revision = 1 if previous is None else int(previous["revision"]) + 1
                payload = ScheduleDefinition(
                    **{**definition.__dict__, "revision": revision}
                ).to_payload()
            entries.append({
                "target_id": target_id,
                "schedule_id": schedule_id,
                "kind": kind,
                "mapping": mapping,
                "actor_kind": actor_kind,
                "actor_id": actor_id,
                "action": action,
                "revision": revision,
                "definition": payload,
            })

    counts = {
        action: sum(1 for entry in entries if entry["action"] == action)
        for action in ("create", "update", "noop", "skip")
    }
    return {
        "service_principal": service_principal,
        "include_paid": include_paid,
        "timezone": timezone,
        "entries": entries,
        "counts": counts,
    }


def apply_watchlist_migration(plan: dict[str, Any]) -> dict[str, Any]:
    applied = []
    for entry in plan["entries"]:
        if entry["action"] not in {"create", "update"}:
            continue
        result = repo.register_schedule(ScheduleDefinition.from_payload(entry["definition"]))
        applied.append({
            "schedule_id": entry["schedule_id"],
            "target_id": entry["target_id"],
            "revision": entry["revision"],
            "created": result["created"],
        })
    return {"applied": applied, "count": len(applied)}


__all__ = ["apply_watchlist_migration", "build_watchlist_migration"]
