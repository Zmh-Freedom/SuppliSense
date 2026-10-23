"""Current-snapshot authorization for procurement case workflows."""

from __future__ import annotations

from typing import Any

from app.db.mongo import get_db
from app.db.postgres import get_cursor
from app.domains.supplier.access import feishu_open_id_for_user

CASE_CAPABILITY_KEYS = (
    "can_read",
    "can_claim",
    "can_assign",
    "can_update",
    "can_request_closure",
    "can_decide_closure",
    "can_reopen",
)
PURCHASER_ROLES = frozenset({"purchaser", "analyst"})
VIEWER_ROLE = "viewer"


def _current_supplier_assignments(supplier_id: str) -> list[dict[str, Any]]:
    """Read active responsibility rows; stale and inactive snapshots never grant scope."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT department_code, purchaser_open_id, manager_open_id
            FROM supplier_responsibility_snapshots
            WHERE supplier_id = %s
              AND source_active = TRUE
              AND sync_status = 'current'
              AND department_code IS NOT NULL
            ORDER BY department_code
            """,
            (str(supplier_id),),
        )
        columns = [description[0] for description in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def _target_id(target: dict[str, Any]) -> str:
    value = target.get("monitor_target_id") or target.get("_id")
    return str(value) if value else ""


def _target_for_case(monitor_target_id: str) -> dict[str, Any] | None:
    document = get_db()["watchlist"].find_one(
        {"monitor_target_id": str(monitor_target_id)}
    )
    if not isinstance(document, dict):
        return None
    target = dict(document)
    target.pop("_id", None)
    return target


def get_case_target(monitor_target_id: str) -> dict[str, Any] | None:
    """Return the current Mongo read model used to re-check a case target."""
    return _target_for_case(monitor_target_id)


def current_manager_departments(user_id: str) -> list[str]:
    """Return departments managed by this active Feishu identity today."""
    try:
        open_id = feishu_open_id_for_user(str(user_id))
        if not open_id:
            return []
        with get_cursor() as (_, cur):
            cur.execute(
                """
                SELECT DISTINCT department_code
                FROM supplier_responsibility_snapshots
                WHERE manager_open_id = %s
                  AND source_active = TRUE
                  AND sync_status = 'current'
                  AND department_code IS NOT NULL
                ORDER BY department_code
                """,
                (str(open_id),),
            )
            return [str(row[0]) for row in cur.fetchall()]
    except Exception:
        return []


def is_current_manager_for_target(target: dict[str, Any], user_id: str) -> bool:
    """Check whether the active responsibility snapshot names this user as a target manager."""
    supplier_id = str(target.get("supplier_id") or "")
    if not supplier_id:
        return False
    try:
        open_id = feishu_open_id_for_user(str(user_id))
        if not open_id:
            return False
        return any(
            str(row.get("manager_open_id") or "") == str(open_id)
            for row in _current_supplier_assignments(supplier_id)
        )
    except Exception:
        return False


def _actor_assignments(
    assignments: list[dict[str, Any]], open_id: str
) -> list[dict[str, Any]]:
    return [
        item
        for item in assignments
        if open_id
        and (
            str(item.get("purchaser_open_id") or "") == open_id
            or str(item.get("manager_open_id") or "") == open_id
        )
    ]


def resolve_case_scope(
    target: dict[str, Any],
    user_id: str,
    user_role: str,
    department_code: str | None,
) -> dict[str, Any]:
    """Resolve a case department from current supplier responsibility rows.

    A client-provided department is only a selector: the current snapshot must
    independently prove the actor is assigned to that department.
    """
    target_id = _target_id(target)
    if not target_id:
        raise PermissionError("监控对象不存在或标识无效")
    if user_role == VIEWER_ROLE or user_role not in PURCHASER_ROLES | {"admin"}:
        raise PermissionError("当前角色不能创建或管理采购事项")

    supplier_id = str(target.get("supplier_id") or "")
    if not supplier_id:
        if user_role == "admin" or str(target.get("owner_user_id") or "") == str(user_id):
            return {
                "target_id": target_id,
                "department_code": None,
                "is_manager": False,
            }
        raise PermissionError("外部或未解析对象仅允许其所有者和管理员处理")

    try:
        assignments = _current_supplier_assignments(supplier_id)
    except Exception as exc:
        raise PermissionError("无法确认供应商当前责任范围") from exc
    if not assignments:
        if user_role == "admin" and department_code is None:
            return {
                "target_id": target_id,
                "department_code": None,
                "is_manager": False,
            }
        raise PermissionError("供应商没有可用的当前科室责任记录")

    open_id = ""
    if user_role == "admin":
        eligible = assignments
    else:
        try:
            open_id = feishu_open_id_for_user(str(user_id))
        except Exception as exc:
            raise PermissionError("无法确认用户当前责任身份") from exc
        if not open_id:
            raise PermissionError("用户没有有效的当前责任身份")
        eligible = _actor_assignments(assignments, str(open_id))
        if not eligible:
            raise PermissionError("当前用户不在供应商责任范围内")

    departments = sorted(
        {
            str(item["department_code"])
            for item in eligible
            if item.get("department_code")
        }
    )
    if department_code:
        selected_department = str(department_code)
        if selected_department not in departments:
            raise PermissionError("所选科室不在当前责任范围内")
    elif len(departments) == 1:
        selected_department = departments[0]
    elif len(departments) > 1:
        raise ValueError("该供应商属于多个可用科室，请明确选择事项科室")
    else:
        raise PermissionError("供应商没有可用的当前科室责任记录")

    is_manager = bool(open_id) and any(
        str(item.get("department_code") or "") == selected_department
        and str(item.get("manager_open_id") or "") == str(open_id)
        for item in eligible
    )
    return {
        "target_id": target_id,
        "department_code": selected_department,
        "is_manager": is_manager,
    }


def _denied_capabilities() -> dict[str, bool]:
    return {key: False for key in CASE_CAPABILITY_KEYS}


def case_capabilities(case: dict[str, Any], user_id: str, user_role: str) -> dict[str, bool]:
    """Return only actions allowed by current object, department, and case scope."""
    denied = _denied_capabilities()
    if user_role == VIEWER_ROLE or user_role not in PURCHASER_ROLES | {"admin"}:
        return denied

    try:
        target_id = str(case.get("monitor_target_id") or "")
        target = _target_for_case(target_id) if target_id else None
        if target is None:
            if user_role != "admin":
                return denied
            scope = {"is_manager": False}
        elif user_role == "admin":
            scope = {"is_manager": False}
        else:
            scope = resolve_case_scope(
                target,
                user_id,
                user_role,
                str(case["department_code"]) if case.get("department_code") else None,
            )
    except Exception:
        return denied

    is_admin = user_role == "admin"
    is_manager = bool(scope.get("is_manager"))
    is_owner = str(case.get("owner_user_id") or "") == str(user_id)
    is_creator = str(case.get("created_by") or "") == str(user_id)
    status = str(case.get("status") or "")
    closure_request = case.get("closure_request")
    closure_request = closure_request if isinstance(closure_request, dict) else {}

    can_read = is_admin or is_manager or is_owner or is_creator
    if not can_read:
        return denied

    is_unassigned_open = status == "open" and not case.get("owner_user_id")
    can_claim = is_unassigned_open and (is_admin or is_manager or is_creator)
    return {
        "can_read": True,
        "can_claim": can_claim,
        "can_assign": status in {"open", "in_progress", "deferred"}
        and (is_admin or is_manager),
        "can_update": is_owner and status in {"in_progress", "deferred"},
        "can_request_closure": is_owner and status == "in_progress",
        "can_decide_closure": (
            status == "pending_closure"
            and (is_admin or is_manager)
            and str(closure_request.get("requested_by") or "") != str(user_id)
        ),
        "can_reopen": status == "closed" and (is_admin or is_manager),
    }
