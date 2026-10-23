"""Authorized procurement queue and current evidence coverage aggregation."""

from __future__ import annotations

import base64
import hashlib
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any

from app.db.mongo import get_db
from app.domains.alert import case_access, case_repo, case_service, service as alert_service

_COUNT_KEYS = ("pending_closure", "overdue", "due_review", "high", "normal")
_GROUP_RANK = {name: index for index, name in enumerate(_COUNT_KEYS)}
_NULL_DUE_SORT = 253402300799.0  # 9999-12-31, finite and stable in JSON cursors.
_ACTION_CAPABILITIES = (
    "can_claim",
    "can_assign",
    "can_update",
    "can_request_closure",
    "can_decide_closure",
    "can_reopen",
)


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _queue_group(case: dict[str, Any], capabilities: dict[str, bool], *, now: datetime) -> str | None:
    """Return the single highest-priority actionable queue group for a case."""
    if str(case.get("status") or "") == "closed":
        return None
    if not any(capabilities.get(key) for key in _ACTION_CAPABILITIES):
        return None
    if str(case.get("status") or "") == "pending_closure" and capabilities.get("can_decide_closure"):
        return "pending_closure"
    due_at = _as_utc(case.get("due_at"))
    if due_at is not None and due_at <= now:
        return "overdue"
    next_review_at = _as_utc(case.get("next_review_at"))
    if next_review_at is not None and next_review_at <= now:
        return "due_review"
    if str(case.get("priority") or "normal") == "high":
        return "high"
    return "normal"


def _aggregate_coverage(records: list[tuple[str, dict[str, Any]]]) -> dict[str, int]:
    by_target: dict[str, set[str]] = {}
    for target_id, coverage in records:
        states = by_target.setdefault(str(target_id), set())
        for dimension in coverage.get("dimensions") or []:
            validity = dimension.get("validity") or {}
            status = str(validity.get("status") or "")
            if status in {"unknown", "conflicting"}:
                states.add("unknown")
            elif status in {"stale"}:
                states.add("stale")
            elif status in {"failed"}:
                states.add("failed")
            elif not status and dimension.get("status") == "pending":
                states.add("unknown")
    return {
        "target_total": len(by_target),
        "unknown_count": sum("unknown" in states for states in by_target.values()),
        "stale_count": sum("stale" in states for states in by_target.values()),
        "failed_count": sum("failed" in states for states in by_target.values()),
    }


def _coverage_target_items(
    records: list[tuple[str, dict[str, Any]]], targets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    names = {
        str(target.get("monitor_target_id")): str(
            target.get("display_name") or target.get("company_name") or target.get("monitor_target_id")
        )
        for target in targets
    }
    items = []
    for target_id, coverage in records:
        dimensions = coverage.get("dimensions") or []
        states = {
            str((dimension.get("validity") or {}).get("status") or "")
            for dimension in dimensions
        }
        unknown = bool(states & {"unknown", "conflicting"}) or any(
            not (dimension.get("validity") or {}).get("status")
            and dimension.get("status") == "pending"
            for dimension in dimensions
        )
        stale = "stale" in states
        failed = "failed" in states
        missing_dimensions = list(coverage.get("missing_dimensions") or [])
        if not (unknown or stale or failed or missing_dimensions or coverage.get("status") != "complete"):
            continue
        items.append({
            "target_id": str(target_id),
            "display_name": names.get(str(target_id), str(target_id)),
            "missing_dimensions": missing_dimensions,
            "unknown": unknown,
            "stale": stale,
            "failed": failed,
        })
    return items


def _visible_targets(user_id: str, user_role: str, *, department_code: str | None = None) -> list[dict[str, Any]]:
    """Read stable target IDs without the legacy read helper's lazy backfill."""
    db = get_db()
    targets: list[dict[str, Any]] = []
    for document in db["watchlist"].find():
        if not isinstance(document, dict):
            continue
        target = alert_service._target_from_document(document)
        target_id = str(target.get("monitor_target_id") or "")
        if not target_id:
            continue
        supplier_id = str(target.get("supplier_id") or "")
        if supplier_id:
            if user_role == "admin" and department_code is None:
                pass
            else:
                target_departments = case_access.current_target_departments(
                    target, user_id, user_role
                )
                if not target_departments:
                    continue
                if department_code and department_code not in target_departments:
                    continue
        elif user_role != "admin" and str(target.get("owner_user_id") or "") != str(user_id):
            continue
        elif department_code:
            # Private and unresolved targets have no Feishu department scope.
            if not supplier_id:
                continue
        targets.append(target)
    return targets


def _coverage_for_targets(
    targets: list[dict[str, Any]],
    user_id: str,
    user_role: str,
    *,
    now: datetime,
) -> list[tuple[str, dict[str, Any]]]:
    db = get_db()
    from app.domains.risk.assessment_contracts import assessment_scope

    records: list[tuple[str, dict[str, Any]]] = []
    with assessment_scope(actor_id=str(user_id), actor_role=str(user_role)):
        for target in targets:
            try:
                snapshots = alert_service._target_snapshots(db, target)
            except Exception:
                snapshots = alert_service._projected_target_history(db, target)
                for snapshot in snapshots:
                    snapshot["_assessment_context_unavailable"] = True
            latest = snapshots[0] if snapshots else None
            coverage = alert_service._data_coverage(db, target, latest, now=now)
            records.append((str(target["monitor_target_id"]), coverage))
    return records


def _scope_token(user_id: str, user_role: str, view: str, department_code: str | None) -> str:
    raw = json.dumps(
        [str(user_id), str(user_role), view, department_code],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _due_for_sort(case: dict[str, Any], group: str) -> datetime | None:
    field = "next_review_at" if group == "due_review" else "due_at"
    return _as_utc(case.get(field))


def _sort_key(case: dict[str, Any], group: str) -> tuple[int, float, float, str]:
    due = _due_for_sort(case, group)
    updated = _as_utc(case.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc)
    return (
        _GROUP_RANK[group],
        due.timestamp() if due is not None else _NULL_DUE_SORT,
        -updated.timestamp(),
        str(case.get("id") or ""),
    )


def _encode_cursor(case: dict[str, Any], group: str, scope_token: str) -> str:
    sort_key = _sort_key(case, group)
    payload = {"scope": scope_token, "key": [sort_key[0], sort_key[1], sort_key[2], sort_key[3]]}
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None, scope_token: str) -> tuple[int, float, float, str] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
        key = payload["key"]
        if payload.get("scope") != scope_token or not isinstance(key, list) or len(key) != 4:
            raise ValueError
        return int(key[0]), float(key[1]), float(key[2]), str(key[3])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("工作台分页游标无效或范围已变化") from exc


def _item(case: dict[str, Any], group: str) -> dict[str, Any]:
    target_id = str(case.get("monitor_target_id") or "")
    case_id = str(case.get("id") or "")
    return {
        "case_id": case_id,
        "target_id": target_id,
        "title": str(case.get("title") or "采购事项"),
        "group": group,
        "status": str(case.get("status") or ""),
        "owner_user_id": str(case["owner_user_id"]) if case.get("owner_user_id") else None,
        "due_at": case.get("due_at"),
        "next_review_at": case.get("next_review_at"),
        "detail_url": f"/cases/{case_id}",
    }


def get_procurement_workbench(
    user_id: str,
    user_role: str,
    *,
    view: str = "mine",
    department_code: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a scoped actionable case queue and current evidence gap counts."""
    if view not in {"mine", "department"}:
        raise ValueError("不支持的工作台范围")
    actor_id, role = str(user_id), str(user_role)
    is_admin = role == "admin"
    manager_departments = case_access.current_manager_departments(actor_id) if not is_admin else []
    admin_departments: list[str] = []
    if is_admin:
        try:
            admin_departments = case_access.current_departments()
        except Exception:
            admin_departments = []
    selected_department = None
    if view == "department":
        if is_admin:
            if not department_code:
                raise ValueError("管理员查看科室工作台时必须指定科室")
            selected_department = str(department_code)
            if admin_departments and selected_department not in admin_departments:
                raise ValueError("所选科室不在当前责任快照范围内")
        else:
            if not manager_departments:
                raise PermissionError("当前用户没有管理科室工作台权限")
            if department_code:
                selected_department = str(department_code)
                if selected_department not in manager_departments:
                    raise PermissionError("所选科室不在当前管理科室范围内")
            elif len(manager_departments) == 1:
                selected_department = manager_departments[0]
            else:
                raise ValueError("请明确选择当前管理科室")
    elif department_code:
        raise ValueError("本人工作台不接受科室参数")

    page_size = max(1, min(int(limit), 100))
    checked_at = _as_utc(now) or datetime.now(timezone.utc)
    scope_token = _scope_token(actor_id, role, view, selected_department)
    available_roles = role in case_access.PURCHASER_ROLES | {"admin"}
    capabilities = {
        "can_view_mine": available_roles,
        "can_view_department": is_admin or bool(manager_departments),
        "can_choose_department": is_admin or len(manager_departments) > 1,
        "department_codes": admin_departments if is_admin else manager_departments,
    }
    counts: dict[str, int | None] = {name: 0 for name in _COUNT_KEYS}
    items: list[dict[str, Any]] = []
    next_cursor = None
    case_status = "available"
    coverage_value: dict[str, int] | None = None
    coverage_status = "available"
    targets: list[dict[str, Any]] = []

    try:
        targets = _visible_targets(actor_id, role, department_code=selected_department)
    except Exception:
        targets = []
        case_status = "unavailable"
        coverage_status = "unavailable"

    if case_status == "available":
        try:
            if not available_roles:
                candidates = []
            else:
                candidates = case_repo.list_workbench_cases(
                    {"view": view, "department_code": selected_department},
                    actor_id=actor_id,
                    user_role=role,
                    target_ids=[str(target["monitor_target_id"]) for target in targets],
                )
            grouped: list[tuple[dict[str, Any], str]] = []
            for case in candidates:
                case_capabilities = case_service._can_read_case(case, actor_id, role)
                if not case_capabilities.get("can_read"):
                    continue
                group = _queue_group(case, case_capabilities, now=checked_at)
                if group is None:
                    continue
                grouped.append((case, group))
                counts[group] += 1  # type: ignore[operator]
            grouped.sort(key=lambda pair: _sort_key(pair[0], pair[1]))
            cursor_key = _decode_cursor(cursor, scope_token)
            if cursor_key is not None:
                grouped = [pair for pair in grouped if _sort_key(pair[0], pair[1]) > cursor_key]
            page = grouped[:page_size]
            items = [_item(case, group) for case, group in page]
            if len(grouped) > page_size and page:
                next_cursor = _encode_cursor(page[-1][0], page[-1][1], scope_token)
        except Exception:
            case_status = "unavailable"
            counts = {name: None for name in _COUNT_KEYS}
            items = []
            next_cursor = None

    if coverage_status == "available":
        try:
            coverage_records = _coverage_for_targets(targets, actor_id, role, now=checked_at)
            coverage_value = {
                **_aggregate_coverage(coverage_records),
                "targets": _coverage_target_items(coverage_records, targets),
            }
        except Exception:
            coverage_status = "unavailable"
            coverage_value = None

    return {
        "scope": {"view": view, "department_code": selected_department},
        "capabilities": capabilities,
        "counts": counts,
        "items": items,
        "next_cursor": next_cursor,
        "coverage": coverage_value,
        "sections": {
            "cases": {"status": case_status, "count": sum(counts.values()) if case_status == "available" else None},
            "coverage": {
                "status": coverage_status,
                "count": coverage_value["target_total"] if coverage_value is not None else None,
            },
        },
    }
