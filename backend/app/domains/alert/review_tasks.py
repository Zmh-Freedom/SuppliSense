"""Persistent procurement review task lifecycle for monitored targets.

The monitoring workbench only displays the latest task.  This module owns the
durable task, approval, execution, evidence and result records so a review can
be resumed after a page refresh or a worker restart.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from psycopg2.extras import Json

from app.db.mongo import get_db
from app.db.postgres import get_cursor

REVIEW_TASK_TYPES = frozenset(
    {"verify_identity", "assess", "review", "supplement_data", "continue_monitoring"}
)
REVIEW_TASK_STATUSES = frozenset(
    {
        "pending_approval",
        "approved",
        "rejected",
        "executing",
        "completed",
        "needs_review",
        "failed",
        "cancelled",
    }
)
ACTIVE_TASK_STATUSES = ("pending_approval", "approved", "executing")


def _iso(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return _iso(value)


def _row(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: _json_safe(value) for key, value in row.items()}
    result.setdefault("evidence_refs", [])
    result.setdefault("evidence_count", len(result["evidence_refs"]))
    return result


def _authorize_case_task(
    row: dict[str, Any],
    user_id: str,
    user_role: str,
    *,
    action: str = "read",
    target: dict[str, Any] | None = None,
) -> None:
    """Apply current case capability checks to a task linked to a business case."""
    from app.domains.alert import case_access, case_repo

    cases = case_repo.list_linked_cases_for_task(str(row.get("id") or ""))
    if not cases:
        if user_role == "admin":
            return
        if (
            user_role in case_access.PURCHASER_ROLES
            and str(row.get("requested_by") or "") == str(user_id)
        ):
            return
        if (
            user_role in case_access.PURCHASER_ROLES
            and target
            and case_access.is_current_manager_for_target(target, str(user_id))
        ):
            return
        raise PermissionError("无权访问该复核任务")
    scoped = [
        (
            case,
            case_access.case_capabilities(case, str(user_id), str(user_role)),
        )
        for case in cases
    ]
    readable = [(case, caps) for case, caps in scoped if caps.get("can_read")]
    if not readable:
        raise PermissionError("无权访问该复核任务")
    if action == "decide":
        if not (
            user_role == "admin"
            or str(row.get("requested_by") or "") == str(user_id)
            or any(caps.get("can_assign") for _, caps in readable)
        ):
            raise PermissionError("无权确认该复核任务")
    elif action == "execute":
        is_case_owner = any(
            str(case.get("owner_user_id") or "") == str(user_id)
            for case, _ in readable
        )
        if not (
            user_role == "admin"
            or str(row.get("requested_by") or "") == str(user_id)
            or is_case_owner
            or any(caps.get("can_assign") for _, caps in readable)
        ):
            raise PermissionError("无权执行该复核任务")


def _authorize_task(
    row: dict[str, Any], user_id: str, user_role: str, *, action: str = "read"
) -> None:
    target = _authorize_target_access(
        str(row.get("monitor_target_id") or ""), user_id, user_role
    )
    _authorize_case_task(row, user_id, user_role, action=action, target=target)


def _target(monitor_target_id: str) -> dict[str, Any] | None:
    document = get_db()["watchlist"].find_one({"monitor_target_id": monitor_target_id})
    if not isinstance(document, dict):
        return None
    document.pop("_id", None)
    return document


def _authorize_target_access(
    monitor_target_id: str,
    user_id: str,
    user_role: str,
    *,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if user_role == "admin":
        return target or _target(monitor_target_id) or {}
    target = target if target is not None else _target(monitor_target_id)
    if target is None:
        raise PermissionError("无权访问该监控对象")
    from app.domains.alert.service import _can_access_target

    if not _can_access_target(target, str(user_id), user_role):
        raise PermissionError("无权访问该监控对象")
    return target


def _append_event(
    cur: Any,
    task_id: UUID,
    actor_id: str | None,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    payload: dict[str, Any] | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO monitor_review_task_events
            (id, task_id, actor_id, event_type, from_status, to_status, payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            str(uuid4()), str(task_id), actor_id, event_type, from_status, to_status,
            Json(_json_safe(payload or {})),
        ),
    )


def _fetch_task(cur: Any, task_id: str | UUID, *, include_history: bool = True) -> dict[str, Any] | None:
    cur.execute("SELECT * FROM monitor_review_tasks WHERE id = %s", (str(task_id),))
    row = cur.fetchone()
    if row is None:
        return None
    columns = [desc[0] for desc in cur.description]
    task = _row(dict(zip(columns, row, strict=True)))
    if include_history:
        cur.execute(
            """
            SELECT id, actor_id, event_type, from_status, to_status, payload, created_at
            FROM monitor_review_task_events WHERE task_id = %s ORDER BY created_at ASC
            """,
            (str(task_id),),
        )
        event_columns = [desc[0] for desc in cur.description]
        task["events"] = [_row(dict(zip(event_columns, event, strict=True))) for event in cur.fetchall()]
        cur.execute(
            """
            SELECT id, monitor_target_id, evidence_id, evidence, created_at
            FROM monitor_review_task_evidence WHERE task_id = %s ORDER BY created_at ASC
            """,
            (str(task_id),),
        )
        evidence_columns = [desc[0] for desc in cur.description]
        evidence = [_row(dict(zip(evidence_columns, item, strict=True))) for item in cur.fetchall()]
        task["evidence"] = [item.get("evidence") or {} for item in evidence]
        task["evidence_count"] = len(evidence)
    return task


def get_review_task(task_id: str, user_id: str, user_role: str) -> dict[str, Any] | None:
    with get_cursor() as (_, cur):
        task = _fetch_task(cur, task_id)
    if task is not None:
        _authorize_task(task, user_id, user_role)
    return task


def get_latest_review_task(
    monitor_target_id: str,
    user_id: str | None = None,
    user_role: str | None = None,
) -> dict[str, Any] | None:
    """Return the latest visible target task for dashboard enrichment."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT id FROM monitor_review_tasks
            WHERE monitor_target_id = %s ORDER BY created_at DESC LIMIT 1
            """,
            (monitor_target_id,),
        )
        row = cur.fetchone()
        task = _fetch_task(cur, row[0], include_history=False) if row else None
    if task is not None and user_id is not None and user_role is not None:
        try:
            _authorize_task(task, user_id, user_role)
        except PermissionError:
            return None
    return task


def _current_user_target_ids(user_id: str, user_role: str) -> list[str]:
    """Find formal monitor targets in the user's current responsibility scope."""
    from app.domains.alert.case_access import PURCHASER_ROLES

    if user_role not in PURCHASER_ROLES:
        return []
    try:
        from app.domains.supplier.access import list_assigned_supplier_ids

        supplier_ids = list_assigned_supplier_ids(str(user_id), str(user_role))
        if not supplier_ids:
            return []
        targets = get_db()["watchlist"].find(
            {"supplier_id": {"$in": sorted(supplier_ids)}},
            {"monitor_target_id": 1},
        )
        return sorted(
            {
                str(target["monitor_target_id"])
                for target in targets
                if target.get("monitor_target_id")
            }
        )
    except Exception:
        return []


def list_review_tasks(
    monitor_target_id: str | None, user_id: str, user_role: str
) -> list[dict[str, Any]]:
    if monitor_target_id:
        _authorize_target_access(monitor_target_id, user_id, user_role)
    clauses: list[str] = []
    values: list[Any] = []
    if monitor_target_id:
        clauses.append("monitor_target_id = %s")
        values.append(monitor_target_id)
    if user_role != "admin":
        from app.domains.alert.case_access import current_manager_departments

        departments = current_manager_departments(user_id)
        target_ids = _current_user_target_ids(user_id, user_role)
        case_clause = (
            "id IN (SELECT link.task_id FROM procurement_case_tasks AS link "
            "JOIN procurement_cases AS cases ON cases.id = link.case_id "
            "WHERE cases.owner_user_id = %s OR cases.created_by = %s"
            + (" OR cases.department_code = ANY(%s)" if departments else "")
            + ")"
        )
        clauses.append(
            f"(requested_by = %s OR {case_clause} OR monitor_target_id = ANY(%s))"
        )
        values.extend([user_id, user_id, user_id])
        if departments:
            values.append(departments)
        values.append(target_ids)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_cursor() as (_, cur):
        cur.execute(
            f"SELECT * FROM monitor_review_tasks {where} ORDER BY created_at DESC LIMIT 200",
            tuple(values),
        )
        columns = [desc[0] for desc in cur.description]
        tasks = [_row(dict(zip(columns, row, strict=True))) for row in cur.fetchall()]
    visible = []
    for task in tasks:
        try:
            _authorize_task(task, user_id, user_role)
            visible.append(task)
        except PermissionError:
            continue
    return visible


def create_review_task(
    monitor_target_id: str,
    task_type: str,
    payload: dict[str, Any],
    requested_by: str,
    user_role: str | None = None,
) -> dict[str, Any]:
    if task_type not in REVIEW_TASK_TYPES:
        raise ValueError("不支持的复核任务类型")
    target = _target(monitor_target_id)
    if target is None:
        raise ValueError("监控对象不存在")
    if user_role is not None:
        _authorize_target_access(
            monitor_target_id, str(requested_by), user_role, target=target
        )
    with get_cursor() as (_, cur):
        return create_review_task_with_cursor(
            cur,
            {
                "monitor_target_id": monitor_target_id,
                "task_type": task_type,
                "payload": payload,
                "requested_by": requested_by,
                "user_role": user_role,
            },
        )


def create_review_task_with_cursor(cur: Any, values: dict[str, Any]) -> dict[str, Any]:
    """Create or reuse one active task using a caller-owned transaction."""
    monitor_target_id = str(values["monitor_target_id"])
    task_type = str(values["task_type"])
    payload = values.get("payload") or {}
    requested_by = str(values["requested_by"])
    user_role = values.get("user_role")
    dedupe_across_requesters = bool(values.get("dedupe_across_requesters"))
    if task_type not in REVIEW_TASK_TYPES:
        raise ValueError("不支持的复核任务类型")
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
        (monitor_target_id, task_type),
    )
    requester_clause = (
        "AND requested_by = %s"
        if user_role is not None and user_role != "admin" and not dedupe_across_requesters
        else ""
    )
    existing_params: list[Any] = [monitor_target_id, task_type]
    if requester_clause:
        existing_params.append(requested_by)
    cur.execute(
        f"""
        SELECT id FROM monitor_review_tasks
        WHERE monitor_target_id = %s AND task_type = %s
          AND status IN ('pending_approval', 'approved', 'executing')
          {requester_clause}
        ORDER BY created_at DESC LIMIT 1
        """,
        tuple(existing_params),
    )
    existing = cur.fetchone()
    if existing:
        task = _fetch_task(cur, existing[0])
        if task is not None:
            return task
    task_id = str(uuid4())
    cur.execute(
        """
        INSERT INTO monitor_review_tasks
          (id, monitor_target_id, task_type, status, requested_by, payload)
        VALUES (%s, %s, %s, 'pending_approval', %s, %s)
        """,
        (task_id, monitor_target_id, task_type, requested_by, Json(_json_safe(payload))),
    )
    _append_event(cur, task_id, requested_by, "created", None, "pending_approval", payload)
    return _fetch_task(cur, task_id) or {}


def decide_review_task(
    task_id: str,
    expected_version: int,
    decision: str,
    comment: str | None,
    user_id: str,
    user_role: str,
) -> dict[str, Any]:
    if decision not in {"approved", "rejected"}:
        raise ValueError("审批决定必须是 approved 或 rejected")
    with get_cursor() as (_, cur):
        cur.execute("SELECT * FROM monitor_review_tasks WHERE id = %s FOR UPDATE", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError("复核任务不存在")
        columns = [desc[0] for desc in cur.description]
        task = _row(dict(zip(columns, row, strict=True)))
        _authorize_task(task, user_id, user_role, action="decide")
        if task["version"] != expected_version:
            raise ValueError("复核任务已发生变化，请刷新后重试")
        if task["status"] != "pending_approval":
            raise ValueError("当前任务不在待审批状态")
        now = datetime.now(timezone.utc)
        status = decision
        cur.execute(
            """
            UPDATE monitor_review_tasks
            SET status = %s, version = version + 1, approved_by = %s,
                approval_comment = %s, approved_at = %s, completed_at = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (status, str(user_id), comment, now, now if decision == "rejected" else None, task_id),
        )
        _append_event(cur, str(task_id), str(user_id), "decision", task["status"], status, {"comment": comment})
        return _fetch_task(cur, task_id) or {}


def _evidence(
    task: dict[str, Any],
    *,
    dimension: str,
    provider: str,
    source_type: str,
    status: str,
    data_mode: str,
    facts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "evidence_id": f"review-task:{task['id']}:{uuid4().hex[:12]}",
        "entity_id": task["monitor_target_id"],
        "monitor_target_id": task["monitor_target_id"],
        "dimension": dimension,
        "provider": provider,
        "source_type": source_type,
        "status": status,
        "data_mode": data_mode,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "facts": _json_safe(facts),
    }


def _execute_action(
    task: dict[str, Any],
    target: dict[str, Any],
    *,
    actor_id: str | None = None,
    actor_role: str | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    from app.domains.risk.assessment_service import monitor_assessment

    task_type = task["task_type"]
    name = str(target.get("display_name") or target.get("company_name") or "").strip()
    if task_type in {"assess", "review"}:
        preview = monitor_assessment(name, monitor_target_id=task["monitor_target_id"],
            company_id=target.get("company_id"),actor_id=task.get("requested_by"),request_key="review-task:"+str(task["id"]))
        if preview is None:
            evidence = _evidence(
                task, dimension="risk_monitoring", provider="monitoring_workbench",
                source_type="risk_snapshot", status="partial", data_mode="formal",
                facts={"company_name": name, "reason": "缺少可用的主体或风险基础数据"},
            )
            return "needs_review", {"summary": "风险评估未形成结论，主体或风险数据当前未覆盖", "status": "needs_review"}, [evidence]
        result = preview.model_dump(mode="json") if hasattr(preview, "model_dump") else dict(preview)
        evidence = _evidence(
            task, dimension="risk_monitoring", provider="cached_risk_evidence",
            source_type="risk_snapshot", status="supported", data_mode="formal",
            facts={"risk_score": result.get("risk_score"), "risk_level": result.get("risk_level"), "summary": result.get("summary")},
        )
        return "completed", {"summary": "已基于现有证据完成风险复核", "status": "completed", "risk_score": result.get("risk_score"), "risk_level": result.get("risk_level")}, [evidence]
    if task_type == "verify_identity":
        evidence = _evidence(
            task, dimension="identity", provider="monitoring_workbench", source_type="watchlist_identity",
            status="partial", data_mode="formal",
            facts={"identity_status": target.get("identity_status"), "supplier_id": target.get("supplier_id"), "candidate_id": target.get("candidate_id"), "company_id": target.get("company_id")},
        )
        return "needs_review", {"summary": "主体核验尚未形成可确认结果", "status": "needs_review"}, [evidence]
    if task_type == "supplement_data":
        coverage = {}
        if actor_id and actor_role:
            try:
                from app.domains.alert.service import get_watchlist_target_summaries

                summary = next(
                    (
                        item
                        for item in get_watchlist_target_summaries(actor_id, actor_role)
                        if str(item.get("monitor_target_id") or "")
                        == str(task["monitor_target_id"])
                    ),
                    None,
                )
                if summary:
                    coverage = summary.get("data_coverage") or {}
            except Exception:
                coverage = {}
        missing_dimensions = coverage.get("missing_dimensions", [])
        current_status = coverage.get("status") or "unknown"
        evidence = _evidence(
            task, dimension="risk_monitoring", provider="monitoring_workbench", source_type="coverage_snapshot",
            status="partial", data_mode="formal",
            facts={
                "coverage_status": current_status,
                "missing_dimensions": missing_dimensions,
                "dimensions": coverage.get("dimensions", []),
            },
        )
        summary = (
            "当前数据覆盖仍不完整，已记录当前未覆盖的数据域"
            if current_status not in {"unknown", "failed"}
            else "当前覆盖状态暂不可用，需人工复核"
        )
        return "needs_review", {
            "summary": summary,
            "status": "needs_review",
            "missing_dimensions": missing_dimensions,
        }, [evidence]
    evidence = _evidence(
        task, dimension="risk_monitoring", provider="monitoring_workbench", source_type="monitoring_observation",
        status="supported", data_mode="formal", facts={"monitor_status": target.get("monitor_status"), "identity_status": target.get("identity_status")},
    )
    return "completed", {"summary": "已记录本轮监控观察结果，继续跟踪后续变化", "status": "completed"}, [evidence]


def execute_review_task(
    task_id: str,
    expected_version: int,
    user_id: str,
    user_role: str,
) -> dict[str, Any]:
    with get_cursor() as (_, cur):
        cur.execute("SELECT * FROM monitor_review_tasks WHERE id = %s FOR UPDATE", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError("复核任务不存在")
        columns = [desc[0] for desc in cur.description]
        task = _row(dict(zip(columns, row, strict=True)))
        _authorize_task(task, user_id, user_role, action="execute")
        if task["version"] != expected_version:
            raise ValueError("复核任务已发生变化，请刷新后重试")
        if task["status"] != "approved":
            raise ValueError("只有已审批任务才能执行")
        cur.execute(
            "UPDATE monitor_review_tasks SET status = 'executing', version = version + 1, started_at = NOW(), updated_at = NOW() WHERE id = %s",
            (task_id,),
        )
        _append_event(cur, str(task_id), str(user_id), "execution_started", "approved", "executing")

    target = _target(task["monitor_target_id"])
    if target is None:
        final_status = "failed"
        result = {"summary": "监控对象已不存在，无法执行复核", "status": "failed"}
        evidences: list[dict[str, Any]] = []
        error_code = "MONITOR_TARGET_NOT_FOUND"
    else:
        try:
            _authorize_task(task, user_id, user_role, action="execute")
        except PermissionError:
            with get_cursor() as (_, cur):
                cur.execute(
                    """
                    UPDATE monitor_review_tasks
                    SET status = 'cancelled', version = version + 1,
                        error_code = 'AUTHORIZATION_REVOKED', completed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s AND status = 'executing'
                    """,
                    (task_id,),
                )
                _append_event(
                    cur, UUID(str(task_id)), str(user_id), "execution_denied",
                    "executing", "cancelled", {"reason": "authorization_revoked"},
                )
                return _fetch_task(cur, task_id) or {}
        try:
            final_status, result, evidences = _execute_action(
                task, target, actor_id=str(user_id), actor_role=str(user_role)
            )
            error_code = None
        except Exception as exc:
            final_status = "failed"
            result = {"summary": "复核执行失败，请查看错误后重试", "status": "failed"}
            evidences = []
            error_code = type(exc).__name__

    with get_cursor() as (_, cur):
        refs = [item["evidence_id"] for item in evidences]
        cur.execute(
            """
            UPDATE monitor_review_tasks
            SET status = %s, version = version + 1, result = %s, evidence_refs = %s,
                error_code = %s, completed_at = NOW(), updated_at = NOW()
            WHERE id = %s
            """,
            (final_status, Json(_json_safe(result)), Json(refs), error_code, task_id),
        )
        for item in evidences:
            cur.execute(
                """
                INSERT INTO monitor_review_task_evidence
                  (id, task_id, monitor_target_id, evidence_id, evidence)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (task_id, evidence_id) DO NOTHING
                """,
                (str(uuid4()), str(task_id), task["monitor_target_id"], item["evidence_id"], Json(_json_safe(item))),
            )
        _append_event(cur, str(task_id), str(user_id), "execution_finished", "executing", final_status, {"error_code": error_code, "evidence_refs": refs})
        return _fetch_task(cur, task_id) or {}
