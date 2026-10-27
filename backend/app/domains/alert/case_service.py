"""Procurement case lifecycle and authorization orchestration."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID

from app.db.postgres import get_cursor
from app.domains.alert import case_access, case_repo


class CaseConflictError(ValueError):
    """A case version or lifecycle state changed since the caller read it."""


class CaseNotFoundError(LookupError):
    """A requested business case no longer exists."""


_COMMAND_FIELDS = {
    "claim": set(),
    "assign": {"owner_user_id"},
    "request_transfer": {"reason"},
    "receipt": {"summary", "refs"},
    "defer": {"reason", "next_review_at"},
    "resume": set(),
    "set_due": {"due_at"},
    "request_closure": {"category", "summary", "receipt_ids"},
    "approve_closure": {"reason"},
    "reject_closure": {"reason"},
    "reopen": {"reason"},
}
_COMMAND_OPTIONAL_FIELDS = {
    "request_closure": {"residual_limitations", "duplicate_case_id"},
}
_CLOSURE_CATEGORIES = {"resolved", "accepted_limitation", "duplicate", "withdrawn"}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}不能为空")
    result = value.strip()
    if len(result) > 2000:
        raise ValueError(f"{field}不能超过2000个字符")
    return result


def _as_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field}必须是有效时间") from exc
    else:
        raise ValueError(f"{field}必须是有效时间")
    if parsed.tzinfo is None:
        raise ValueError(f"{field}必须包含时区")
    return parsed.astimezone(timezone.utc)


def _evidence_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return _as_datetime(value, "collected_at")
    except ValueError:
        return None


def _validate_command(command: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(command, dict):
        raise ValueError("命令格式无效")
    name = command.get("command")
    if name not in _COMMAND_FIELDS:
        raise ValueError("不支持的事项命令")
    expected = command.get("expected_version")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        raise ValueError("expected_version 必须是大于0的整数")
    allowed = {"command", "expected_version", *_COMMAND_FIELDS[name]}
    allowed.update(_COMMAND_OPTIONAL_FIELDS.get(name, set()))
    unexpected = set(command) - allowed
    if unexpected:
        raise ValueError(f"该命令不接受字段：{', '.join(sorted(unexpected))}")
    missing = _COMMAND_FIELDS[name] - set(command)
    if missing:
        raise ValueError(f"命令缺少必需字段：{', '.join(sorted(missing))}")

    normalized = dict(command)
    for field in ("reason", "summary", "residual_limitations"):
        if field in normalized:
            normalized[field] = _text(normalized[field], field)
    if name == "assign" and not str(normalized.get("owner_user_id") or "").strip():
        raise ValueError("owner_user_id不能为空")
    if name == "assign":
        try:
            normalized["owner_user_id"] = str(UUID(str(normalized["owner_user_id"])))
        except ValueError as exc:
            raise ValueError("owner_user_id必须是有效账号标识") from exc
    if name == "receipt":
        refs = normalized.get("refs")
        if not isinstance(refs, list) or not all(isinstance(ref, str) and ref.strip() for ref in refs):
            raise ValueError("refs必须是非空字符串列表")
        normalized["refs"] = list(dict.fromkeys(ref.strip() for ref in refs))
    if name == "request_closure":
        category = normalized.get("category")
        if category not in _CLOSURE_CATEGORIES:
            raise ValueError("不支持的关闭类别")
        receipt_ids = normalized.get("receipt_ids")
        if not isinstance(receipt_ids, list) or not receipt_ids or not all(
            isinstance(item, str) and item.strip() for item in receipt_ids
        ):
            raise ValueError("关闭申请必须引用至少一条回执")
        normalized["receipt_ids"] = list(dict.fromkeys(item.strip() for item in receipt_ids))
        if category == "accepted_limitation" and not str(
            normalized.get("residual_limitations") or ""
        ).strip():
            raise ValueError("accepted_limitation 必须说明残余限制")
        if category == "duplicate" and not str(normalized.get("duplicate_case_id") or "").strip():
            raise ValueError("duplicate 关闭申请必须指定目标事项")
        if category != "duplicate" and normalized.get("duplicate_case_id") is not None:
            raise ValueError("仅 duplicate 关闭申请接受 duplicate_case_id")
        if category != "accepted_limitation" and normalized.get("residual_limitations") is not None:
            raise ValueError("仅 accepted_limitation 接受 residual_limitations")
    if name == "defer":
        normalized["next_review_at"] = _as_datetime(
            normalized["next_review_at"], "next_review_at"
        )
    if name == "set_due" and normalized["due_at"] is not None:
        normalized["due_at"] = _as_datetime(normalized["due_at"], "due_at")
    return normalized


def _validate_closure_request(
    case: dict[str, Any],
    request: dict[str, Any],
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    del case
    category = request.get("category")
    if category not in _CLOSURE_CATEGORIES:
        raise ValueError("不支持的关闭类别")
    summary = _text(request.get("summary"), "summary")
    receipt_ids = request.get("receipt_ids")
    available_receipts = {
        str(event.get("id"))
        for event in receipts
        if event.get("event_type") == "receipt_recorded" and event.get("id")
    }
    if not isinstance(receipt_ids, list) or not receipt_ids:
        raise ValueError("关闭申请必须引用至少一条回执")
    if not set(map(str, receipt_ids)).issubset(available_receipts):
        raise ValueError("关闭申请只能引用本事项已记录的回执")
    normalized = {
        "category": category,
        "summary": summary,
        "receipt_ids": list(dict.fromkeys(map(str, receipt_ids))),
    }
    if category == "accepted_limitation":
        normalized["residual_limitations"] = _text(
            request.get("residual_limitations"), "residual_limitations"
        )
    elif request.get("residual_limitations") is not None:
        raise ValueError("仅 accepted_limitation 接受 residual_limitations")
    if category == "duplicate":
        duplicate_case_id = str(request.get("duplicate_case_id") or "").strip()
        if not duplicate_case_id:
            raise ValueError("duplicate 关闭申请必须指定目标事项")
        normalized["duplicate_case_id"] = duplicate_case_id
    elif request.get("duplicate_case_id") is not None:
        raise ValueError("仅 duplicate 关闭申请接受 duplicate_case_id")
    return normalized


def _validate_resolved_closure(
    case: dict[str, Any],
    request: dict[str, Any],
    receipts: list[dict[str, Any]],
    current_evidence: dict[str, Any] | set[str],
    *,
    closure_baseline: dict[str, Any] | None = None,
) -> None:
    if request.get("category") != "resolved":
        return
    receipt_ids = set(map(str, request.get("receipt_ids") or []))
    relevant_receipts = [
        event
        for event in receipts
        if str(event.get("id")) in receipt_ids
        and event.get("event_type") == "receipt_recorded"
    ]
    referenced = {
        str(ref)
        for event in relevant_receipts
        for ref in (event.get("payload") or {}).get("refs", [])
    }
    if isinstance(current_evidence, dict):
        current_evidence_ids = set(current_evidence.get("evidence_ids") or set())
        evidence_by_dimension = current_evidence.get("evidence_by_dimension") or {}
    else:
        current_evidence_ids = set(current_evidence)
        evidence_by_dimension = {}
    valid_references = referenced.intersection(current_evidence_ids)
    if case.get("case_type") == "coverage_gap":
        missing_dimensions = current_evidence.get("missing_dimensions") if isinstance(current_evidence, dict) else None
        if (
            not isinstance(current_evidence, dict)
            or current_evidence.get("coverage_status") != "complete"
            or not isinstance(missing_dimensions, list)
            or missing_dimensions
        ):
            raise ValueError("当前数据覆盖仍不完整，不能以 resolved 关闭")
        if not isinstance(closure_baseline, dict):
            raise ValueError("缺少创建时覆盖缺口证据基线，不能以 resolved 关闭")
        captured_at = _evidence_timestamp(closure_baseline.get("captured_at"))
        missing_at_creation = closure_baseline.get("missing_dimension_keys")
        baseline_by_dimension = closure_baseline.get("evidence_by_dimension")
        if (
            captured_at is None
            or not isinstance(missing_at_creation, list)
            or not missing_at_creation
            or not isinstance(baseline_by_dimension, dict)
        ):
            raise ValueError("创建时没有可核验的原覆盖缺口，不能以 resolved 关闭")
        collected_at_by_id = current_evidence.get("evidence_collected_at") or {}
        if not isinstance(collected_at_by_id, dict):
            collected_at_by_id = {}
        for dimension in map(str, missing_at_creation):
            current_ids = set(map(str, evidence_by_dimension.get(dimension) or []))
            baseline_ids = set(map(str, baseline_by_dimension.get(dimension) or []))
            new_valid_ids = {
                evidence_id
                for evidence_id in current_ids - baseline_ids
                if (
                    (collected_at := _evidence_timestamp(collected_at_by_id.get(evidence_id)))
                    is not None
                    and collected_at > captured_at
                )
            }
            new_references = valid_references.intersection(new_valid_ids)
            if not new_references:
                raise ValueError(
                    "resolved 关闭必须引用创建后取得的原缺口对应有效证据"
                )
            receipt_after_evidence = set()
            for receipt in relevant_receipts:
                receipt_at = _evidence_timestamp(receipt.get("created_at"))
                if receipt_at is None:
                    continue
                receipt_refs = set(
                    map(str, (receipt.get("payload") or {}).get("refs", []))
                )
                receipt_after_evidence.update(
                    evidence_id
                    for evidence_id in new_references.intersection(receipt_refs)
                    if (
                        (collected_at := _evidence_timestamp(collected_at_by_id.get(evidence_id)))
                        is not None
                        and collected_at <= receipt_at
                    )
                )
            if not receipt_after_evidence:
                raise ValueError("引用原缺口证据的处置回执时间早于证据采集时间")
        return
    if case.get("case_type") == "identity_review":
        identity_evidence = set(evidence_by_dimension.get("identity") or set())
        valid_references = referenced.intersection(identity_evidence)
    if not valid_references:
        kind = "当前有效新证据或已核验主体记录"
        if case.get("case_type") == "identity_review":
            kind = "当前已核验的主体记录"
        raise ValueError(f"resolved 关闭必须引用{kind}")


def _validate_duplicate_target(
    current_case: dict[str, Any],
    duplicate_case: dict[str, Any],
    lookup_case: Callable[[str], dict[str, Any] | None],
) -> None:
    current_id = str(current_case.get("id") or "")
    duplicate_id = str(duplicate_case.get("id") or "")
    if not current_id or current_id == duplicate_id:
        raise ValueError("事项不能重复指向自身")
    if (
        str(current_case.get("monitor_target_id") or "")
        != str(duplicate_case.get("monitor_target_id") or "")
        or str(current_case.get("scope_key") or "")
        != str(duplicate_case.get("scope_key") or "")
    ):
        raise ValueError("duplicate 目标必须属于同一监控对象和事项范围")
    visited = {current_id}
    cursor: dict[str, Any] | None = duplicate_case
    while cursor is not None:
        candidate_id = str(cursor.get("id") or "")
        if candidate_id in visited:
            raise ValueError("duplicate 引用不能形成循环")
        if not candidate_id:
            break
        visited.add(candidate_id)
        closure = cursor.get("closure_request")
        closure = closure if isinstance(closure, dict) else {}
        if closure.get("category") != "duplicate":
            break
        next_id = str(closure.get("duplicate_case_id") or "")
        if not next_id:
            raise ValueError("duplicate 目标事项引用无效")
        cursor = lookup_case(next_id)
        if cursor is None:
            raise ValueError("duplicate 引用中的目标事项不存在")


def _has_capability(capabilities: dict[str, bool], name: str) -> None:
    if not capabilities.get(name):
        raise PermissionError("当前用户无权执行该事项操作")


def _require_status(case: dict[str, Any], *statuses: str) -> None:
    if str(case.get("status") or "") not in statuses:
        raise CaseConflictError("事项当前状态已变化，请刷新后重试")


def _plan_case_command(
    case: dict[str, Any],
    command: dict[str, Any],
    user_id: str,
    user_role: str,
    capabilities: dict[str, bool],
    *,
    now: datetime | None = None,
    receipts: list[dict[str, Any]] | None = None,
    current_evidence: dict[str, Any] | set[str] | None = None,
    closure_baseline: dict[str, Any] | None = None,
    duplicate_lookup: Callable[[str], dict[str, Any] | None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    del user_role
    normalized = command if command.get("command") == "review_task_linked" else _validate_command(command)
    at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    status = str(case.get("status") or "")
    name = normalized["command"]
    changes: dict[str, Any] = {}
    event_type = name
    payload: dict[str, Any] = {}
    to_status = status

    if name == "claim":
        _require_status(case, "open")
        if case.get("owner_user_id"):
            raise CaseConflictError("已分派的事项不能认领")
        _has_capability(capabilities, "can_claim")
        changes = {"status": "in_progress", "owner_user_id": str(user_id)}
        event_type, to_status = "claimed", "in_progress"
    elif name == "assign":
        _require_status(case, "open", "in_progress", "deferred")
        _has_capability(capabilities, "can_assign")
        owner = str(normalized.get("owner_user_id") or "").strip()
        if not owner:
            raise ValueError("owner_user_id不能为空")
        changes = {"owner_user_id": owner}
        if status in {"open", "deferred"}:
            changes["status"] = "in_progress"
            to_status = "in_progress"
        event_type = "assigned"
        payload["owner_user_id"] = owner
    elif name == "request_transfer":
        _require_status(case, "in_progress")
        _has_capability(capabilities, "can_update")
        event_type = "transfer_requested"
        payload["reason"] = normalized["reason"]
    elif name == "receipt":
        _require_status(case, "in_progress")
        _has_capability(capabilities, "can_update")
        event_type = "receipt_recorded"
        payload = {"summary": normalized["summary"], "refs": normalized["refs"]}
    elif name == "defer":
        _require_status(case, "in_progress")
        _has_capability(capabilities, "can_update")
        next_review_at = normalized["next_review_at"]
        if next_review_at <= at:
            raise ValueError("next_review_at必须晚于当前时间")
        changes = {"status": "deferred", "next_review_at": next_review_at}
        event_type, to_status = "deferred", "deferred"
        payload["reason"] = normalized["reason"]
        payload["next_review_at"] = next_review_at.isoformat()
    elif name == "resume":
        _require_status(case, "deferred")
        _has_capability(capabilities, "can_update")
        changes = {"status": "in_progress", "next_review_at": None}
        event_type, to_status = "resumed", "in_progress"
    elif name == "set_due":
        _require_status(case, "open", "in_progress", "deferred")
        if not capabilities.get("can_update") and not capabilities.get("can_assign"):
            raise PermissionError("当前用户无权设置事项期限")
        changes = {"due_at": normalized["due_at"]}
        event_type = "due_date_changed"
        payload["due_at"] = (
            normalized["due_at"].isoformat() if normalized["due_at"] else None
        )
    elif name == "request_closure":
        _require_status(case, "in_progress")
        _has_capability(capabilities, "can_request_closure")
        request = _validate_closure_request(case, normalized, receipts or [])
        if request["category"] == "resolved":
            _validate_resolved_closure(
                case,
                request,
                receipts or [],
                current_evidence or set(),
                closure_baseline=closure_baseline,
            )
        if request["category"] == "duplicate":
            duplicate_id = request["duplicate_case_id"]
            duplicate_case = duplicate_lookup(duplicate_id) if duplicate_lookup else None
            if duplicate_case is None:
                raise ValueError("duplicate 目标事项不存在")
            _validate_duplicate_target(
                case, duplicate_case, duplicate_lookup or (lambda _case_id: None)
            )
        request.update({"requested_by": str(user_id), "requested_at": at.isoformat()})
        changes = {"status": "pending_closure", "closure_request": request}
        event_type, to_status = "closure_requested", "pending_closure"
        payload["closure_request"] = request
    elif name == "approve_closure":
        _require_status(case, "pending_closure")
        _has_capability(capabilities, "can_decide_closure")
        request = case.get("closure_request")
        request = request if isinstance(request, dict) else {}
        if str(request.get("requested_by") or "") == str(user_id):
            raise PermissionError("关闭申请人不能审核自己的申请")
        _validate_closure_request(case, request, receipts or [])
        if request.get("category") == "resolved":
            _validate_resolved_closure(
                case,
                request,
                receipts or [],
                current_evidence or set(),
                closure_baseline=closure_baseline,
            )
        if request.get("category") == "duplicate":
            duplicate_id = str(request.get("duplicate_case_id") or "")
            duplicate_case = duplicate_lookup(duplicate_id) if duplicate_lookup else None
            if duplicate_case is None:
                raise ValueError("duplicate 目标事项不存在")
            _validate_duplicate_target(
                case, duplicate_case, duplicate_lookup or (lambda _case_id: None)
            )
        request = {
            **request,
            "reviewed_by": str(user_id),
            "reviewed_at": at.isoformat(),
            "review_reason": normalized["reason"],
        }
        changes = {"status": "closed", "closed_at": at, "closure_request": request}
        event_type, to_status = "closure_approved", "closed"
        payload.update({"reason": normalized["reason"], "closure_request": request})
    elif name == "reject_closure":
        _require_status(case, "pending_closure")
        _has_capability(capabilities, "can_decide_closure")
        request = case.get("closure_request") or {}
        if str(request.get("requested_by") or "") == str(user_id):
            raise PermissionError("关闭申请人不能审核自己的申请")
        changes = {"status": "in_progress", "closure_request": None}
        event_type, to_status = "closure_rejected", "in_progress"
        payload.update({"reason": normalized["reason"], "rejected_request": request})
    elif name == "reopen":
        _require_status(case, "closed")
        _has_capability(capabilities, "can_reopen")
        changes = {"status": "in_progress", "closed_at": None, "closure_request": None}
        event_type, to_status = "reopened", "in_progress"
        payload["reason"] = normalized["reason"]
    elif name == "review_task_linked":
        if not capabilities.get("can_read"):
            raise PermissionError("当前用户无权关联技术复核任务")
        event_type = "review_task_linked"
        payload["task_id"] = str(normalized.get("task_id") or "")
    else:
        raise ValueError("不支持的事项命令")

    event = {
        "actor_id": str(user_id),
        "event_type": event_type,
        "from_status": status,
        "to_status": to_status,
        "payload": payload,
    }
    return changes, event


def _current_evidence_snapshot(
    case: dict[str, Any], user_id: str, user_role: str
) -> dict[str, Any]:
    """Collect current valid evidence identifiers, timestamps, and coverage state."""
    from app.domains.alert.service import get_watchlist_target_summaries

    unavailable = {
        "evidence_ids": set(),
        "coverage_status": None,
        "missing_dimensions": None,
        "missing_dimension_keys": None,
        "evidence_by_dimension": {},
        "observed_evidence_by_dimension": {},
        "evidence_collected_at": {},
    }
    try:
        summaries = get_watchlist_target_summaries(user_id, user_role)
    except Exception:
        return unavailable
    target_id = str(case.get("monitor_target_id") or "")
    summary = next(
        (
            item
            for item in summaries
            if str(item.get("monitor_target_id") or "") == target_id
        ),
        None,
    )
    if not summary:
        return unavailable
    coverage = summary.get("data_coverage") or {}
    evidence_by_dimension: dict[str, set[str]] = {}
    observed_evidence_by_dimension: dict[str, set[str]] = {}
    evidence_collected_at: dict[str, str] = {}
    dimension_statuses: dict[str, str] = {}
    evidence_reference_keys = {
        "assessment_id",
        "evidence_id",
        "source_id",
        "snapshot_id",
        "source_version",
    }

    def source_timestamp(value: dict[str, Any]) -> datetime | None:
        for field in ("collected_at", "checked_at", "synced_at"):
            timestamp = _evidence_timestamp(value.get(field))
            if timestamp is not None:
                return timestamp
        return None

    def latest_collected_at(value: Any) -> datetime | None:
        if isinstance(value, dict):
            timestamps = [
                timestamp
                for timestamp in (
                    source_timestamp(value),
                    *(latest_collected_at(child) for child in value.values()),
                )
                if timestamp is not None
            ]
            return max(timestamps) if timestamps else None
        if isinstance(value, list):
            timestamps = [
                timestamp
                for timestamp in (latest_collected_at(child) for child in value)
                if timestamp is not None
            ]
            return max(timestamps) if timestamps else None
        return None

    def collect(value: Any, key: str = "", inherited_at: datetime | None = None) -> None:
        if isinstance(value, dict):
            collected_at = source_timestamp(value) or inherited_at
            for child_key, child in value.items():
                child_key = str(child_key)
                if child_key in evidence_reference_keys and child:
                    evidence_id = str(child)
                    references.add(evidence_id)
                    previous = _evidence_timestamp(evidence_collected_at.get(evidence_id))
                    if collected_at is not None and (previous is None or collected_at > previous):
                        evidence_collected_at[evidence_id] = collected_at.isoformat()
                else:
                    collect(child, child_key, collected_at)
        elif isinstance(value, list):
            for child in value:
                collect(child, key, inherited_at)

    for dimension in coverage.get("dimensions", []):
        dimension_key = str(dimension.get("key") or "")
        dimension_statuses[dimension_key] = str(dimension.get("status") or "unknown")
        references: set[str] = set()
        validity = dimension.get("validity") or {}
        collect(validity)
        collect(dimension.get("facts") or {}, inherited_at=latest_collected_at(validity))
        if dimension_key == "identity" and summary.get("company_id"):
            # Keep this for identity-review closure; coverage gaps exclude identity evidence.
            references.add(str(summary["company_id"]))
        observed_evidence_by_dimension[dimension_key] = references
        if dimension.get("status") == "available":
            evidence_by_dimension[dimension_key] = references
    return {
        "evidence_ids": set().union(*evidence_by_dimension.values()) if evidence_by_dimension else set(),
        "coverage_status": coverage.get("status"),
        "missing_dimensions": coverage.get("missing_dimensions"),
        "missing_dimension_keys": [
            key for key, status in dimension_statuses.items() if status != "available"
        ],
        "evidence_by_dimension": evidence_by_dimension,
        "observed_evidence_by_dimension": observed_evidence_by_dimension,
        "evidence_collected_at": evidence_collected_at,
    }


def _coverage_closure_baseline(snapshot: dict[str, Any]) -> dict[str, Any]:
    evidence_by_dimension = (
        snapshot.get("observed_evidence_by_dimension")
        or snapshot.get("evidence_by_dimension")
        or {}
    )
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "missing_dimension_keys": snapshot.get("missing_dimension_keys"),
        "evidence_by_dimension": {
            str(dimension): sorted(map(str, evidence_ids or []))
            for dimension, evidence_ids in evidence_by_dimension.items()
        },
    }


def _can_read_case(case: dict[str, Any], user_id: str, user_role: str) -> dict[str, bool]:
    return case_access.case_capabilities(case, str(user_id), str(user_role))


def _can_read_case_for_workbench(
    case: dict[str, Any], user_id: str, user_role: str
) -> dict[str, bool]:
    return case_access.case_capabilities_strict(case, str(user_id), str(user_role))


def _require_current_case_read(
    case: dict[str, Any] | None, user_id: str, user_role: str
) -> dict[str, bool]:
    if case is None:
        raise CaseNotFoundError("采购事项不存在")
    capabilities = _can_read_case(case, user_id, user_role)
    if not capabilities.get("can_read"):
        raise PermissionError("无权访问该采购事项")
    return capabilities


def _decode_cursor(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        cursor = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("分页游标无效") from exc
    if not isinstance(cursor, dict) or not cursor.get("updated_at") or not cursor.get("id"):
        raise ValueError("分页游标无效")
    return {
        "updated_at": _as_datetime(cursor["updated_at"], "cursor.updated_at"),
        "id": str(cursor["id"])[:64],
    }


def _encode_cursor(cursor: dict[str, Any] | None) -> str | None:
    if cursor is None:
        return None
    value = json.dumps(cursor, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def create_case(
    payload: dict[str, Any], user_id: str, user_role: str, request_key: str
) -> dict[str, Any]:
    allowed = {
        "monitor_target_id", "case_type", "department_code", "title", "priority", "due_at",
    }
    if set(payload) - allowed:
        raise ValueError("事项创建请求含不支持字段")
    target_id = str(payload.get("monitor_target_id") or "").strip()
    target = case_access.get_case_target(target_id)
    if target is None:
        raise ValueError("监控对象不存在")
    scope = case_access.resolve_case_scope(
        target,
        str(user_id),
        str(user_role),
        str(payload["department_code"]) if payload.get("department_code") else None,
    )
    case_type = str(payload.get("case_type") or "")
    if case_type not in case_repo.CASE_TYPES:
        raise ValueError("不支持的采购事项类型")
    department = scope.get("department_code")
    scope_key = f"{scope['target_id']}:{department or 'private'}:{case_type}:default"
    values = {
        "monitor_target_id": scope["target_id"],
        "company_id": str(target["company_id"]) if target.get("company_id") else None,
        "department_code": department,
        "case_type": case_type,
        "scope_key": scope_key,
        "trigger_key": "manual",
        "title": _text(str(payload.get("title") or ""), "title"),
        "priority": str(payload.get("priority") or "normal"),
        "created_by": str(user_id),
        "owner_user_id": None,
        "due_at": (
            _as_datetime(payload["due_at"], "due_at")
            if payload.get("due_at") is not None
            else None
        ),
        "next_review_at": None,
    }
    created_event_payload = None
    if case_type == "coverage_gap":
        initial_evidence = _current_evidence_snapshot(
            {"monitor_target_id": scope["target_id"]}, str(user_id), str(user_role)
        )
        created_event_payload = {
            "closure_baseline": _coverage_closure_baseline(initial_evidence)
        }
    created = case_repo.create_or_get_case(
        values,
        str(user_id),
        request_key,
        created_event_payload=created_event_payload,
    )
    return {
        "case": created,
        "capabilities": _can_read_case(created, user_id, user_role),
    }


def get_case_detail(case_id: str, user_id: str, user_role: str) -> dict[str, Any] | None:
    case = case_repo.get_case(case_id)
    if case is None:
        return None
    capabilities = _can_read_case(case, user_id, user_role)
    if not capabilities.get("can_read"):
        return None
    return {
        "case": case,
        "events": case_repo.list_case_events(case_id),
        "linked_tasks": case_repo.list_case_tasks(case_id),
        "capabilities": capabilities,
    }


def list_cases(filters: dict[str, Any], user_id: str, user_role: str) -> dict[str, Any]:
    allowed_filters = {
        "status",
        "case_type",
        "monitor_target_id",
        "owner_user_id",
        "view",
        "limit",
        "cursor",
    }
    if set(filters) - allowed_filters:
        raise ValueError("事项查询包含不支持的过滤条件")
    view = str(filters.get("view") or "accessible")
    if view not in {"mine", "department", "accessible"}:
        raise ValueError("不支持的事项视图")
    if filters.get("status") and filters["status"] not in case_repo.CASE_STATUSES:
        raise ValueError("不支持的事项状态")
    if filters.get("case_type") and filters["case_type"] not in case_repo.CASE_TYPES:
        raise ValueError("不支持的采购事项类型")
    limit = max(1, min(int(filters.get("limit", 20)), 100))
    cursor = _decode_cursor(filters.get("cursor"))
    manager_departments = case_access.current_manager_departments(str(user_id))
    repository_filters = {
        **{
            key: value
            for key, value in filters.items()
            if key in allowed_filters - {"limit", "cursor"}
        },
        "view": view,
    }
    visible: list[dict[str, Any]] = []
    scan_cursor = cursor
    scan_limit = 100
    # SQL applies a coarse owner/department filter; fill pages after current target-level checks.
    while True:
        page = case_repo.list_cases(
            repository_filters,
            actor_id=str(user_id),
            user_role=str(user_role),
            manager_departments=manager_departments,
            limit=scan_limit,
            cursor=scan_cursor,
        )
        for case in page["items"]:
            capabilities = _can_read_case(case, user_id, user_role)
            if capabilities.get("can_read"):
                visible.append({"case": case, "capabilities": capabilities})
                if len(visible) > limit:
                    break
        if len(visible) > limit or not page["items"] or not page["next_cursor"]:
            break
        next_scan_cursor = page["next_cursor"]
        if next_scan_cursor == scan_cursor:
            break
        scan_cursor = next_scan_cursor

    page_items = visible[:limit]
    next_cursor = None
    if len(visible) > limit and page_items:
        last_case = page_items[-1]["case"]
        updated_at = last_case["updated_at"]
        next_cursor = _encode_cursor(
            {
                "updated_at": updated_at.isoformat()
                if isinstance(updated_at, datetime)
                else updated_at,
                "id": str(last_case["id"]),
            }
        )
    can_list_cases = str(user_role) in case_access.PURCHASER_ROLES | {"admin"}
    return {
        "items": page_items,
        "next_cursor": next_cursor,
        "available_views": {
            "mine": can_list_cases,
            "department": can_list_cases and bool(manager_departments),
            "accessible": str(user_role) == "admin",
        },
    }


def _case_events(case_id: str, *, cur: object) -> list[dict[str, Any]]:
    return case_repo.list_case_events(case_id, cur=cur)


def _require_assignable_user(
    case: dict[str, Any], owner_user_id: str, *, cur: Any
) -> None:
    cur.execute("SELECT role, is_active FROM users WHERE id = %s", (str(owner_user_id),))
    row = cur.fetchone()
    if not row or not row[1] or row[0] not in {"purchaser", "analyst"}:
        raise ValueError("负责人必须是活跃的采购员账号")
    target = case_access.get_case_target(str(case.get("monitor_target_id") or ""))
    if target is None:
        raise PermissionError("监控对象当前不可用")
    try:
        case_access.resolve_case_scope(
            target,
            str(owner_user_id),
            str(row[0]),
            str(case["department_code"]) if case.get("department_code") else None,
        )
    except (PermissionError, ValueError) as exc:
        raise PermissionError("被指派人不在该事项当前对象范围内") from exc


def _require_task_command_access(
    case: dict[str, Any], user_id: str, user_role: str
) -> dict[str, bool]:
    capabilities = _can_read_case(case, user_id, user_role)
    if not capabilities.get("can_read"):
        raise PermissionError("无权访问该采购事项")
    if not (capabilities.get("can_update") or capabilities.get("can_assign")):
        raise PermissionError("当前用户不能为该事项创建复核任务")
    target = case_access.get_case_target(str(case.get("monitor_target_id") or ""))
    try:
        from app.domains.alert.review_tasks import _authorize_target_access

        _authorize_target_access(
            str(case.get("monitor_target_id") or ""),
            str(user_id),
            str(user_role),
            target=target,
        )
    except PermissionError:
        raise
    return capabilities


def handle_case_command(
    case_id: str,
    command: dict[str, Any],
    user_id: str,
    user_role: str,
    request_key: str,
) -> dict[str, Any]:
    normalized = _validate_command(command)
    request_values = {
        "action": "case_command",
        "case_id": str(case_id),
        "command": normalized,
    }
    with get_cursor() as (_, cur):
        request_id, replay = case_repo._begin_request(
            cur, str(user_id), request_key, request_values
        )
        if replay is not None:
            current = case_repo.get_case(case_id, cur=cur)
            capabilities = _require_current_case_read(current, user_id, user_role)
            return {**replay, "capabilities": capabilities}
        case = case_repo.get_case(case_id, cur=cur, for_update=True)
        capabilities = _require_current_case_read(case, user_id, user_role)
        if int(case.get("version") or 0) != normalized["expected_version"]:
            raise CaseConflictError("事项版本已变化，请刷新后重试")
        if normalized["command"] == "assign":
            _require_assignable_user(case, normalized["owner_user_id"], cur=cur)
        events = _case_events(case_id, cur=cur)
        receipts = [
            event for event in events if event.get("event_type") == "receipt_recorded"
        ]
        created_event = next(
            (event for event in events if event.get("event_type") == "created"), None
        )
        created_payload = (created_event or {}).get("payload") or {}
        closure_baseline = created_payload.get("closure_baseline")
        current_evidence: dict[str, Any] = {}
        if normalized["command"] in {"request_closure", "approve_closure"}:
            current_evidence = _current_evidence_snapshot(case, user_id, user_role)

        def lookup_duplicate(duplicate_id: str) -> dict[str, Any] | None:
            target_case = case_repo.get_case(duplicate_id, cur=cur)
            if target_case is None:
                return None
            duplicate_caps = _can_read_case(target_case, user_id, user_role)
            if not duplicate_caps.get("can_read"):
                raise PermissionError("无权访问 duplicate 目标事项")
            return target_case

        changes, event = _plan_case_command(
            case,
            normalized,
            user_id,
            user_role,
            capabilities,
            receipts=receipts,
            current_evidence=current_evidence,
            closure_baseline=closure_baseline,
            duplicate_lookup=lookup_duplicate,
        )
        event["request_id"] = request_id
        updated = case_repo.apply_case_transition(
            case_id, normalized["expected_version"], changes, event, cur=cur
        )
        if updated is None:
            raise CaseConflictError("事项版本已变化，请刷新后重试")
        response = {
            "case": updated,
            "event_id": updated.get("event_id"),
            "capabilities": _can_read_case(updated, user_id, user_role),
        }
        case_repo._complete_request(cur, request_id, case_id, response)
        return response


def create_linked_review_task(
    case_id: str,
    request: dict[str, Any],
    user_id: str,
    user_role: str,
    request_key: str,
) -> dict[str, Any]:
    task_type = str(request.get("task_type") or "")
    payload = request.get("payload") or {}
    from app.domains.alert.review_tasks import REVIEW_TASK_TYPES, create_review_task_with_cursor

    if task_type not in REVIEW_TASK_TYPES:
        raise ValueError("不支持的复核任务类型")
    request_values = {
        "action": "create_linked_review_task",
        "case_id": str(case_id),
        "task_type": task_type,
        "payload": payload,
    }
    with get_cursor() as (_, cur):
        request_id, replay = case_repo._begin_request(
            cur, str(user_id), request_key, request_values
        )
        if replay is not None:
            current = case_repo.get_case(case_id, cur=cur)
            capabilities = _require_current_case_read(current, user_id, user_role)
            replayed_task = replay.get("task")
            if isinstance(replayed_task, dict):
                from app.domains.alert.review_tasks import _authorize_task

                _authorize_task(replayed_task, str(user_id), str(user_role))
            return {**replay, "capabilities": capabilities}
        case = case_repo.get_case(case_id, cur=cur, for_update=True)
        if case is None:
            raise CaseNotFoundError("采购事项不存在")
        _require_task_command_access(case, user_id, user_role)
        task = create_review_task_with_cursor(
            cur,
            {
                "monitor_target_id": str(case["monitor_target_id"]),
                "task_type": task_type,
                "payload": payload,
                "requested_by": str(user_id),
                "user_role": str(user_role),
                "dedupe_across_requesters": True,
            },
        )
        from app.domains.alert.review_tasks import _authorize_task

        _authorize_task(task, str(user_id), str(user_role))
        case_repo.link_case_task(case_id, str(task["id"]), user_id, cur=cur)
        updated = case_repo.apply_case_transition(
            case_id,
            int(case["version"]),
            {},
            {
                "actor_id": str(user_id),
                "event_type": "review_task_linked",
                "from_status": case.get("status"),
                "to_status": case.get("status"),
                "payload": {"task_id": str(task["id"]), "task_type": task_type},
                "request_id": request_id,
            },
            cur=cur,
        )
        if updated is None:
            raise CaseConflictError("事项版本已变化，请刷新后重试")
        response = {
            "case": updated,
            "task": task,
            "event_id": updated.get("event_id"),
            "capabilities": _can_read_case(updated, user_id, user_role),
        }
        case_repo._complete_request(cur, request_id, case_id, response)
        return response
