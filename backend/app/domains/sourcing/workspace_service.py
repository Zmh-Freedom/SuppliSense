"""Authorized, cross-source read model for the procurement sourcing workspace."""

import base64
import binascii
import json
from datetime import datetime, timezone
from typing import Any

from app.core.config import agent_run_v2_route, settings
from app.core.errors import DomainError
from app.core.rollout_gate import get_rollout_state_snapshot
from app.domains.agent_run.repo import list_sourcing_risk_runs_with_cursor
from app.domains.sourcing.repo import list_requests_with_cursor

SOURCE_TYPES = frozenset({"agent_run", "legacy_request"})


def list_sourcing_tasks(
    user_id: str,
    user_role: str,
    *,
    cursor: str | None,
    limit: int = 20,
    scope: str = "mine",
) -> dict[str, Any]:
    """Return a stable page from the authorized run and legacy-request sources."""
    if not 1 <= limit <= 100:
        raise DomainError("SOURCING_WORKSPACE_LIMIT_INVALID", "任务数量必须在 1 到 100 之间", 400)
    if scope not in {"mine", "all"}:
        raise DomainError("SOURCING_WORKSPACE_SCOPE_INVALID", "任务范围无效", 400)
    if scope == "all" and user_role != "admin":
        raise DomainError("SOURCING_WORKSPACE_SCOPE_FORBIDDEN", "只有管理员可以切换全局任务范围", 403)

    cursor_payload = _decode_cursor(cursor) if cursor else None
    scope_all = scope == "all"
    sources: dict[str, dict[str, str]] = {}
    collected: list[dict[str, Any]] = []
    source_failed = False

    run_state = _run_source_state(user_id, user_role)
    if run_state == "available":
        try:
            collected.extend(list_sourcing_risk_runs_with_cursor(
                user_id=user_id,
                scope_all=scope_all,
                cursor=cursor_payload,
                limit=limit + 1,
            ))
            sources["agent_run"] = {"status": "available"}
        except Exception:
            source_failed = True
            sources["agent_run"] = {"status": "unavailable", "reason": "智能寻源历史暂不可用"}
    else:
        if run_state == "unavailable":
            source_failed = True
        sources["agent_run"] = {
            "status": run_state,
            "reason": "当前灰度未开放智能寻源历史" if run_state == "disabled" else "智能寻源控制状态暂不可用",
        }

    try:
        collected.extend(list_requests_with_cursor(
            user_id=user_id,
            scope_all=scope_all,
            cursor=cursor_payload,
            limit=limit + 1,
        ))
        sources["legacy_request"] = {"status": "available"}
    except Exception:
        source_failed = True
        sources["legacy_request"] = {"status": "unavailable", "reason": "兼容寻源历史暂不可用"}

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in collected:
        source_type = str(item.get("source_type") or "")
        source_id = str(item.get("source_id") or "")
        if source_type not in SOURCE_TYPES or not source_id:
            continue
        normalized = {**item, "source_type": source_type, "source_id": source_id}
        normalized["updated_at"] = _normalize_timestamp(item.get("updated_at"))
        normalized.setdefault("time_basis", "updated_at")
        unique[(source_type, source_id)] = normalized

    ordered = sorted(unique.values(), key=_sort_key)
    page = ordered[:limit]
    next_cursor = None
    if not source_failed and len(ordered) > limit and page:
        next_cursor = _encode_cursor(page[-1])
    return {"items": page, "next_cursor": next_cursor, "sources": sources}


def _run_source_state(user_id: str, user_role: str) -> str:
    snapshot = get_rollout_state_snapshot()
    if snapshot is None:
        return "unavailable"
    if snapshot.get("state") == "rollback_frozen":
        return "disabled"
    route = agent_run_v2_route(
        user_id,
        user_role,
        settings,
        rollout=snapshot.get("stage"),
        rollout_state=snapshot.get("state"),
    )
    return "disabled" if route == "legacy" else "available"


def _decode_cursor(value: str) -> dict[str, str]:
    """Decode and validate the public opaque keyset cursor before any source read."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise DomainError("SOURCING_WORKSPACE_CURSOR_INVALID", "任务分页游标无效，请刷新后重试", 400)
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"updated_at", "source_type", "source_id"}:
            raise ValueError("invalid cursor fields")
        if payload["source_type"] not in SOURCE_TYPES:
            raise ValueError("invalid cursor source")
        if not isinstance(payload["source_id"], str) or not payload["source_id"] or len(payload["source_id"]) > 255:
            raise ValueError("invalid cursor id")
        if not isinstance(payload["updated_at"], str):
            raise ValueError("invalid cursor timestamp")
        timestamp = datetime.fromisoformat(payload["updated_at"])
        if timestamp.tzinfo is None:
            raise ValueError("cursor timestamp needs a timezone")
        payload["updated_at"] = timestamp.astimezone(timezone.utc).isoformat()
        return payload
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise DomainError("SOURCING_WORKSPACE_CURSOR_INVALID", "任务分页游标无效，请刷新后重试", 400) from exc


def _encode_cursor(item: dict[str, Any]) -> str:
    payload = {
        "updated_at": _normalize_timestamp(item.get("updated_at")),
        "source_type": item["source_type"],
        "source_id": item["source_id"],
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return encoded.decode("ascii").rstrip("=")


def _normalize_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError as exc:
            raise DomainError("SOURCING_WORKSPACE_SOURCE_INVALID", "寻源任务时间格式无效", 503) from exc
    else:
        raise DomainError("SOURCING_WORKSPACE_SOURCE_INVALID", "寻源任务缺少排序时间", 503)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat()


def _sort_key(item: dict[str, Any]) -> tuple[float, str, str]:
    timestamp = datetime.fromisoformat(str(item["updated_at"]))
    return (-timestamp.timestamp(), str(item["source_type"]), str(item["source_id"]))
