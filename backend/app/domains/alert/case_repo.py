"""PostgreSQL persistence for procurement cases and immutable events."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg2.extras import Json

from app.db.postgres import get_cursor

CASE_TYPES = frozenset({"risk_review", "coverage_gap", "identity_review"})
CASE_PRIORITIES = frozenset({"high", "normal"})
CASE_STATUSES = frozenset(
    {"open", "in_progress", "deferred", "pending_closure", "closed"}
)
_CASE_MUTABLE_FIELDS = frozenset(
    {"status", "owner_user_id", "due_at", "next_review_at", "closure_request", "closed_at"}
)


class IdempotencyConflict(ValueError):
    """The same actor-scoped key was reused for a different request."""


def _json_safe(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pg_json(value: Any) -> Json:
    return Json(_json_safe(value), dumps=_json_dumps)


def _request_hash(values: dict[str, Any]) -> str:
    canonical = _json_dumps(_json_safe(values)).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _row(cur: Any, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return _json_safe(value)
    columns = [description[0] for description in cur.description]
    return _json_safe(dict(zip(columns, value, strict=True)))


def _begin_request(
    cur: Any,
    actor_id: str,
    request_key: str,
    request_values: dict[str, Any],
    *,
    request_hash: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    if not str(request_key or "").strip():
        raise ValueError("必须提供幂等键")
    digest = request_hash or _request_hash(request_values)
    cur.execute(
        """
        INSERT INTO procurement_case_requests
            (id, actor_id, request_key, request_hash)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (actor_id, request_key) DO NOTHING
        RETURNING id
        """,
        (str(uuid4()), str(actor_id), str(request_key), digest),
    )
    inserted = cur.fetchone()
    if inserted:
        return str(inserted[0]), None

    cur.execute(
        """
        SELECT id, request_hash, response
        FROM procurement_case_requests
        WHERE actor_id = %s AND request_key = %s
        FOR UPDATE
        """,
        (str(actor_id), str(request_key)),
    )
    existing = cur.fetchone()
    if existing is None:
        raise RuntimeError("幂等请求在并发写入后不可见")
    request_id, existing_hash, response = existing
    if str(existing_hash) != digest:
        raise IdempotencyConflict("同一幂等键不能用于不同请求")
    if response is None:
        raise IdempotencyConflict("同一幂等键对应的请求尚未完成")
    if not isinstance(response, dict):
        raise RuntimeError("已保存的幂等响应格式无效")
    return str(request_id), _json_safe(response)


def _complete_request(
    cur: Any, request_id: str, case_id: str, response: dict[str, Any]
) -> None:
    cur.execute(
        """
        UPDATE procurement_case_requests
        SET case_id = %s, response = %s, updated_at = NOW()
        WHERE id = %s
        """,
        (str(case_id), _pg_json(response), str(request_id)),
    )
    if getattr(cur, "rowcount", 1) != 1:
        raise RuntimeError("无法保存幂等请求回执")


def _normalise_case_values(values: dict[str, Any], actor_id: str) -> dict[str, Any]:
    monitor_target_id = str(values.get("monitor_target_id") or "").strip()
    case_type = str(values.get("case_type") or "")
    scope_key = str(values.get("scope_key") or "").strip()
    trigger_key = str(values.get("trigger_key") or "manual").strip()
    title = str(values.get("title") or "").strip()
    priority = str(values.get("priority") or "normal")
    created_by = str(values.get("created_by") or actor_id)
    requested_status = str(values.get("status") or "open")
    if not monitor_target_id:
        raise ValueError("监控对象标识不能为空")
    if case_type not in CASE_TYPES:
        raise ValueError("不支持的采购事项类型")
    if not scope_key or not trigger_key or not title:
        raise ValueError("事项范围、触发标识和标题不能为空")
    if priority not in CASE_PRIORITIES:
        raise ValueError("不支持的事项优先级")
    if requested_status != "open":
        raise ValueError("新事项必须以 open 状态创建")
    if created_by != str(actor_id):
        raise ValueError("事项创建人必须与当前操作者一致")
    return {
        "monitor_target_id": monitor_target_id,
        "company_id": str(values["company_id"]) if values.get("company_id") else None,
        "department_code": str(values["department_code"]) if values.get("department_code") else None,
        "case_type": case_type,
        "scope_key": scope_key,
        "trigger_key": trigger_key,
        "title": title,
        "priority": priority,
        "status": "open",
        "created_by": str(actor_id),
        "owner_user_id": str(values["owner_user_id"]) if values.get("owner_user_id") else None,
        "due_at": values.get("due_at"),
        "next_review_at": values.get("next_review_at"),
    }


def _fetch_case(cur: Any, case_id: str, *, for_update: bool = False) -> dict[str, Any] | None:
    query = "SELECT * FROM procurement_cases WHERE id = %s"
    if for_update:
        query += " FOR UPDATE"
    cur.execute(query, (str(case_id),))
    return _row(cur, cur.fetchone())


def get_case(
    case_id: str,
    *,
    cur: object | None = None,
    for_update: bool = False,
) -> dict[str, Any] | None:
    if cur is not None:
        return _fetch_case(cur, case_id, for_update=for_update)
    with get_cursor() as (_, transaction_cursor):
        return _fetch_case(transaction_cursor, case_id, for_update=for_update)


def _find_active_case(cur: Any, values: dict[str, Any]) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT * FROM procurement_cases
        WHERE monitor_target_id = %s
          AND case_type = %s
          AND scope_key = %s
          AND trigger_key = %s
          AND status <> 'closed'
        ORDER BY created_at DESC, id
        LIMIT 1
        FOR UPDATE
        """,
        (
            values["monitor_target_id"],
            values["case_type"],
            values["scope_key"],
            values["trigger_key"],
        ),
    )
    return _row(cur, cur.fetchone())


def _find_previous_case(cur: Any, values: dict[str, Any]) -> str | None:
    cur.execute(
        """
        SELECT id FROM procurement_cases
        WHERE monitor_target_id = %s
          AND case_type = %s
          AND scope_key = %s
          AND trigger_key = %s
          AND status = 'closed'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        FOR UPDATE
        """,
        (
            values["monitor_target_id"],
            values["case_type"],
            values["scope_key"],
            values["trigger_key"],
        ),
    )
    row = cur.fetchone()
    return str(row[0]) if row else None


def _append_event(
    cur: Any,
    *,
    case_id: str,
    actor_id: str | None,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    payload: dict[str, Any] | None,
    version: int,
    request_id: str | None = None,
) -> str:
    event_id = str(uuid4())
    cur.execute(
        """
        INSERT INTO procurement_case_events
            (id, case_id, actor_id, event_type, from_status, to_status,
             payload, request_id, version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event_id,
            str(case_id),
            str(actor_id) if actor_id is not None else None,
            str(event_type),
            from_status,
            to_status,
            _pg_json(payload or {}),
            str(request_id) if request_id else None,
            int(version),
        ),
    )
    return event_id


def _create_case_with_cursor(
    cur: Any,
    values: dict[str, Any],
    actor_id: str,
    request_key: str,
    *,
    created_event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_values = {"action": "create_case", "case": values}
    request_id, replay = _begin_request(cur, actor_id, request_key, request_values)
    if replay is not None:
        return replay

    existing = _find_active_case(cur, values)
    if existing is not None:
        _complete_request(cur, request_id, str(existing["id"]), existing)
        return existing

    previous_case_id = _find_previous_case(cur, values)
    case_id = str(uuid4())
    cur.execute(
        """
        INSERT INTO procurement_cases
            (id, monitor_target_id, company_id, department_code, case_type,
             scope_key, trigger_key, title, priority, status, created_by,
             owner_user_id, due_at, next_review_at, version,
             reopened_from_case_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', %s, %s, %s, %s, 1, %s)
        ON CONFLICT (monitor_target_id, case_type, scope_key, trigger_key)
            WHERE status <> 'closed'
        DO NOTHING
        RETURNING *
        """,
        (
            case_id,
            values["monitor_target_id"],
            values["company_id"],
            values["department_code"],
            values["case_type"],
            values["scope_key"],
            values["trigger_key"],
            values["title"],
            values["priority"],
            values["created_by"],
            values["owner_user_id"],
            values["due_at"],
            values["next_review_at"],
            previous_case_id,
        ),
    )
    created = _row(cur, cur.fetchone())
    if created is None:
        existing = _find_active_case(cur, values)
        if existing is None:
            raise RuntimeError("并发创建事项后未找到活动事项")
        _complete_request(cur, request_id, str(existing["id"]), existing)
        return existing

    _append_event(
        cur,
        case_id=str(created["id"]),
        actor_id=actor_id,
        event_type="created",
        from_status=None,
        to_status="open",
        payload={
            "reopened_from_case_id": previous_case_id,
            **(created_event_payload or {}),
        },
        version=1,
        request_id=request_id,
    )
    _complete_request(cur, request_id, str(created["id"]), created)
    return created


def create_or_get_case(
    values: dict[str, Any],
    actor_id: str,
    request_key: str,
    *,
    cur: object | None = None,
    created_event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _normalise_case_values(values, actor_id)
    if cur is not None:
        return _create_case_with_cursor(
            cur,
            normalized,
            actor_id,
            request_key,
            created_event_payload=created_event_payload,
        )
    with get_cursor() as (_, transaction_cursor):
        return _create_case_with_cursor(
            transaction_cursor,
            normalized,
            actor_id,
            request_key,
            created_event_payload=created_event_payload,
        )


def apply_case_transition(
    case_id: str,
    expected_version: int,
    changes: dict[str, Any],
    event: dict[str, Any],
    *,
    cur: object,
) -> dict[str, Any] | None:
    if expected_version < 1:
        raise ValueError("事项版本必须大于0")
    unexpected = set(changes) - _CASE_MUTABLE_FIELDS
    if unexpected:
        raise ValueError(f"不允许更新事项字段：{', '.join(sorted(unexpected))}")

    transaction_cursor = cur
    current = _fetch_case(transaction_cursor, case_id, for_update=True)
    if current is None or int(current.get("version") or 0) != expected_version:
        return None

    assignments: list[str] = []
    parameters: list[Any] = []
    for field, value in changes.items():
        assignments.append(f"{field} = %s")
        if field == "closure_request" and value is not None:
            value = _pg_json(value)
        elif field == "owner_user_id" and value is not None:
            value = str(value)
        parameters.append(value)
    assignments.extend(["version = version + 1", "updated_at = NOW()"])
    parameters.extend([str(case_id), expected_version])
    transaction_cursor.execute(
        f"""
        UPDATE procurement_cases
        SET {', '.join(assignments)}
        WHERE id = %s AND version = %s
        RETURNING *
        """,
        tuple(parameters),
    )
    updated = _row(transaction_cursor, transaction_cursor.fetchone())
    if updated is None:
        return None

    event_id = _append_event(
        transaction_cursor,
        case_id=str(case_id),
        actor_id=event.get("actor_id"),
        event_type=str(event.get("event_type") or ""),
        from_status=event.get("from_status", current.get("status")),
        to_status=event.get("to_status", updated.get("status")),
        payload=event.get("payload") or {},
        version=int(updated["version"]),
        request_id=event.get("request_id"),
    )
    updated["event_id"] = event_id
    return updated


def link_case_task(
    case_id: str,
    task_id: str,
    actor_id: str,
    *,
    cur: object,
) -> bool:
    transaction_cursor = cur
    transaction_cursor.execute(
        """
        INSERT INTO procurement_case_tasks (id, case_id, task_id, linked_by)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (case_id, task_id) DO NOTHING
        """,
        (str(uuid4()), str(case_id), str(task_id), str(actor_id)),
    )
    return getattr(transaction_cursor, "rowcount", 1) == 1


def list_case_events(case_id: str, *, cur: object | None = None) -> list[dict[str, Any]]:
    def _list(transaction_cursor: Any) -> list[dict[str, Any]]:
        transaction_cursor.execute(
            """
            SELECT * FROM procurement_case_events
            WHERE case_id = %s
            ORDER BY version ASC
            """,
            (str(case_id),),
        )
        columns = [description[0] for description in transaction_cursor.description]
        return [
            _json_safe(dict(zip(columns, row, strict=True)))
            for row in transaction_cursor.fetchall()
        ]

    if cur is not None:
        return _list(cur)
    with get_cursor() as (_, transaction_cursor):
        return _list(transaction_cursor)


def list_case_tasks(case_id: str, *, cur: object | None = None) -> list[dict[str, Any]]:
    def _list(transaction_cursor: Any) -> list[dict[str, Any]]:
        transaction_cursor.execute(
            """
            SELECT link.id, link.case_id, link.task_id, link.linked_by, link.created_at,
                   task.task_type, task.status, task.version, task.result,
                   task.evidence_refs, task.updated_at
            FROM procurement_case_tasks AS link
            JOIN monitor_review_tasks AS task ON task.id = link.task_id
            WHERE link.case_id = %s
            ORDER BY link.created_at ASC, link.id
            """,
            (str(case_id),),
        )
        columns = [description[0] for description in transaction_cursor.description]
        return [
            _json_safe(dict(zip(columns, row, strict=True)))
            for row in transaction_cursor.fetchall()
        ]

    if cur is not None:
        return _list(cur)
    with get_cursor() as (_, transaction_cursor):
        return _list(transaction_cursor)


def get_linked_case_for_task(
    task_id: str, *, cur: object | None = None
) -> dict[str, Any] | None:
    """Return the business case linked to a technical review task, if any."""
    cases = list_linked_cases_for_task(task_id, cur=cur)
    return cases[0] if cases else None


def list_linked_cases_for_task(
    task_id: str, *, cur: object | None = None
) -> list[dict[str, Any]]:
    """Return every business case associated with a technical task."""
    def _get(transaction_cursor: Any) -> list[dict[str, Any]]:
        transaction_cursor.execute(
            """
            SELECT cases.*
            FROM procurement_cases AS cases
            JOIN procurement_case_tasks AS links ON links.case_id = cases.id
            WHERE links.task_id = %s
            ORDER BY links.created_at ASC, links.id
            """,
            (str(task_id),),
        )
        columns = [description[0] for description in transaction_cursor.description]
        return [
            _json_safe(dict(zip(columns, row, strict=True)))
            for row in transaction_cursor.fetchall()
        ]

    if cur is not None:
        return _get(cur)
    with get_cursor() as (_, transaction_cursor):
        return _get(transaction_cursor)


def list_cases(
    filters: dict[str, Any],
    *,
    actor_id: str,
    user_role: str,
    manager_departments: list[str],
    limit: int = 20,
    cursor: dict[str, Any] | None = None,
    cur: object | None = None,
) -> dict[str, Any]:
    """List cases with actor scope and keyset paging applied in SQL."""
    page_size = max(1, min(int(limit), 100))

    def _list(transaction_cursor: Any) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        view = str(filters.get("view") or "accessible")
        if view == "mine":
            clauses.append("(created_by = %s OR owner_user_id = %s)")
            params.extend([str(actor_id), str(actor_id)])
        elif view == "department":
            if manager_departments:
                clauses.append("department_code = ANY(%s)")
                params.append(manager_departments)
            else:
                clauses.append("FALSE")
        elif user_role != "admin":
            scope_parts = ["created_by = %s", "owner_user_id = %s"]
            params.extend([str(actor_id), str(actor_id)])
            if manager_departments:
                scope_parts.append("department_code = ANY(%s)")
                params.append(manager_departments)
            clauses.append(f"({' OR '.join(scope_parts)})")

        for field in ("status", "case_type", "monitor_target_id", "owner_user_id"):
            value = filters.get(field)
            if value:
                clauses.append(f"{field} = %s")
                params.append(value)
        if cursor and cursor.get("updated_at") and cursor.get("id"):
            clauses.append("(updated_at < %s OR (updated_at = %s AND id > %s))")
            params.extend([cursor["updated_at"], cursor["updated_at"], cursor["id"]])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        transaction_cursor.execute(
            f"""
            SELECT * FROM procurement_cases
            {where}
            ORDER BY updated_at DESC, id ASC
            LIMIT %s
            """,
            tuple([*params, page_size + 1]),
        )
        columns = [description[0] for description in transaction_cursor.description]
        rows = [
            _json_safe(dict(zip(columns, row, strict=True)))
            for row in transaction_cursor.fetchall()
        ]
        has_more = len(rows) > page_size
        page = rows[:page_size]
        next_cursor = None
        if has_more and page:
            next_cursor = {
                "updated_at": page[-1]["updated_at"].isoformat()
                if isinstance(page[-1].get("updated_at"), datetime)
                else page[-1].get("updated_at"),
                "id": str(page[-1]["id"]),
            }
        return {"items": page, "next_cursor": next_cursor}

    if cur is not None:
        return _list(cur)
    with get_cursor() as (_, transaction_cursor):
        return _list(transaction_cursor)


def list_workbench_cases(
    filters: dict[str, Any],
    *,
    actor_id: str,
    user_role: str,
    target_ids: list[str],
    cur: object | None = None,
) -> list[dict[str, Any]]:
    """Load active workbench candidates after applying the requested SQL scope.

    The service performs current object capability checks before computing queue
    counts or slicing a page. This query deliberately has no LIMIT so a page of
    stale or newly revoked rows cannot hide later authorized cases.
    """
    view = str(filters.get("view") or "mine")
    department_code = filters.get("department_code")
    if view not in {"mine", "department"}:
        raise ValueError("不支持的工作台范围")
    if view == "department" and not department_code:
        raise ValueError("科室工作台必须指定科室")
    if not target_ids:
        return []

    clauses = ["status <> 'closed'", "monitor_target_id = ANY(%s)"]
    params: list[Any] = [list(map(str, target_ids))]
    if view == "mine":
        clauses.append("(created_by = %s OR owner_user_id = %s)")
        params.extend([str(actor_id), str(actor_id)])
    else:
        clauses.append("department_code = %s")
        params.append(str(department_code))

    def _list(transaction_cursor: Any) -> list[dict[str, Any]]:
        transaction_cursor.execute(
            f"""
            SELECT * FROM procurement_cases
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, id ASC
            """,
            tuple(params),
        )
        columns = [description[0] for description in transaction_cursor.description]
        return [
            _json_safe(dict(zip(columns, row, strict=True)))
            for row in transaction_cursor.fetchall()
        ]

    if cur is not None:
        return _list(cur)
    with get_cursor() as (_, transaction_cursor):
        return _list(transaction_cursor)
