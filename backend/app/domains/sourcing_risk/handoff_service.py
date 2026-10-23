"""Authorized sourcing candidate handoff service."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.core.errors import DomainError
from app.domains.sourcing_risk import handoff_repo

_ALLOWED_ROLES = {"admin", "analyst", "purchaser"}
_HANDOFFABLE_RUN_STATUSES = {"COMPLETED", "PARTIAL", "NEEDS_REVIEW", "ACTION_FAILED"}


def create_candidate_handoff(
    payload: dict[str, Any], user_id: str, user_role: str, request_key: str,
) -> dict[str, Any]:
    """Validate source ownership/version and create an idempotent owner-bound session."""
    if user_role not in _ALLOWED_ROLES:
        raise DomainError("SOURCING_HANDOFF_FORBIDDEN", "当前角色不可创建寻源交接", 403)
    if not request_key.strip():
        raise DomainError("SOURCING_HANDOFF_IDEMPOTENCY_REQUIRED", "缺少交接幂等键", 422)
    request_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    # Idempotent retries must keep their original result, even if the source
    # run advances after the first successful request.
    get_by_request = getattr(handoff_repo, "get_handoff_for_request", None)
    if callable(get_by_request):
        existing = get_by_request(user_id, request_key)
        if existing is not None:
            if str(existing.get("request_hash") or "") != request_hash:
                raise DomainError("SOURCING_HANDOFF_IDEMPOTENCY_CONFLICT", "交接幂等键已用于其他请求", 409)
            return _creation_response(existing)

    run_id = str(payload.get("run_id") or "")
    candidate_key = str(payload.get("candidate_key") or "")
    source = handoff_repo.read_handoff_source(run_id, candidate_key, user_id, user_role)
    if source is None or not _source_owned_by(source, user_id):
        raise DomainError("SOURCING_HANDOFF_SOURCE_NOT_FOUND", "寻源任务或候选不存在，或无权访问", 404)

    run = source.get("run") or {}
    candidate = source.get("candidate") or {}
    if str(run.get("run_type") or "") != "sourcing_risk_v2":
        raise DomainError("SOURCING_HANDOFF_SOURCE_NOT_FOUND", "只能从寻源风险任务创建交接", 404)
    if str(run.get("status") or "").upper() not in _HANDOFFABLE_RUN_STATUSES:
        raise DomainError("SOURCING_HANDOFF_RUN_NOT_READY", "寻源任务尚未完成，暂不能交接候选", 409)
    if str(candidate.get("candidate_key") or candidate.get("id") or "") != candidate_key:
        raise DomainError("SOURCING_HANDOFF_CANDIDATE_NOT_FOUND", "候选已不存在或不属于该寻源任务", 404)
    if int(run.get("version") or 0) != int(payload.get("expected_version") or 0):
        raise DomainError("SOURCING_HANDOFF_VERSION_CONFLICT", "寻源任务已更新，请刷新后重新交接", 409)

    selected_company_id, selected_name = _confirmed_identity(source, "")
    if selected_company_id and not can_access_handoff_candidate(
        selected_company_id, selected_name, user_id, user_role,
    ):
        raise DomainError("SOURCING_HANDOFF_CANDIDATE_FORBIDDEN", "当前用户无权访问该供应商", 403)

    try:
        result = handoff_repo.create_handoff_transaction(
            source, payload, user_id, user_role, request_key, request_hash,
        )
    except handoff_repo.SourcingHandoffConflict as exc:
        raise DomainError("SOURCING_HANDOFF_VERSION_CONFLICT", str(exc), 409) from exc
    return _creation_response(result)


def get_candidate_handoff(handoff_id: str, user_id: str) -> dict[str, Any] | None:
    binding = handoff_repo.get_handoff_for_user(handoff_id, user_id)
    if binding is None:
        return None
    return _public_detail(binding)


def resolve_session_handoff(
    session_id: str, user_id: str, execution_context: dict[str, Any],
) -> dict[str, Any]:
    """Revalidate and merge the authorized handoff into the one loaded context."""
    handoff_id = str(execution_context.get("_requested_handoff_id") or "").strip()
    if not handoff_id:
        return execution_context
    binding = handoff_repo.get_handoff_by_session(session_id)
    if (
        binding is None
        or str(binding.get("handoff_id") or "") != handoff_id
        or str(binding.get("session_id") or "") != session_id
        or str(binding.get("user_id") or "") != user_id
    ):
        raise DomainError("SOURCING_HANDOFF_NOT_FOUND", "交接不存在或无权访问", 404)

    user_role = str(execution_context.get("agent_user_role") or "")
    if user_role not in _ALLOWED_ROLES:
        raise DomainError("SOURCING_HANDOFF_FORBIDDEN", "当前角色不可执行寻源交接", 403)
    if str(binding.get("status") or "") == "cancelled":
        raise DomainError("SOURCING_HANDOFF_CANCELLED", "寻源交接已取消", 409)

    message = str(execution_context.get("_handoff_request_message") or "")
    if str(binding.get("status") or "") == "consumed":
        message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
        if str(binding.get("consumed_message_hash") or "") != message_hash:
            raise DomainError("SOURCING_HANDOFF_ALREADY_CONSUMED", "该交接已执行，请以普通聊天继续后续问题", 409)
        return {
            **execution_context,
            "sourcing_handoff": {
                "handoff_id": handoff_id,
                "session_id": session_id,
                "source_run_id": str(binding.get("source_run_id") or ""),
                "source_run_version": int(binding.get("source_run_version") or 0),
                "candidate_key": str(binding.get("candidate_key") or ""),
                "intent": str(binding.get("intent") or "risk_review"),
                "status": "consumed",
                "consumed_turn_id": str(binding["consumed_turn_id"]) if binding.get("consumed_turn_id") else None,
                "run_id": str(binding["run_id"]) if binding.get("run_id") else None,
            },
        }

    source = handoff_repo.read_handoff_source(
        str(binding.get("source_run_id") or ""),
        str(binding.get("candidate_key") or ""),
        user_id,
        user_role,
    )
    if source is None or not _source_owned_by(source, user_id):
        raise DomainError("SOURCING_HANDOFF_SOURCE_NOT_FOUND", "原寻源任务已不可访问", 404)
    run = source.get("run") or {}
    if int(run.get("version") or 0) != int(binding.get("source_run_version") or 0):
        raise DomainError("SOURCING_HANDOFF_VERSION_CONFLICT", "原寻源任务已更新，请返回任务页重新交接", 409)
    candidate = source.get("candidate") or {}
    if str(candidate.get("candidate_key") or candidate.get("id") or "") != str(binding.get("candidate_key") or ""):
        raise DomainError("SOURCING_HANDOFF_CANDIDATE_NOT_FOUND", "原候选已不可用，请重新选择候选", 404)

    selected_company_id, selected_name = _confirmed_identity(source, message)
    if selected_company_id and not can_access_handoff_candidate(
        selected_company_id, selected_name, user_id, user_role,
    ):
        raise DomainError("SOURCING_HANDOFF_CANDIDATE_FORBIDDEN", "当前用户已无权访问该供应商", 403)

    identity_candidates = _identity_candidates(candidate)
    requires_confirmation = not bool(selected_company_id)
    policy = run.get("policy") or binding.get("source_policy") or {}
    evidence_refs = _evidence_references(
        source.get("evidence_refs") or [],
        freshness_days=policy.get("freshness_days") if isinstance(policy, dict) else None,
    )
    handoff = {
        "handoff_id": handoff_id,
        "session_id": session_id,
        "source_run_id": str(binding.get("source_run_id") or ""),
        "source_run_version": int(binding.get("source_run_version") or 0),
        "candidate_key": str(binding.get("candidate_key") or ""),
        "candidate": _candidate_snapshot(candidate),
        "requirement": dict(run.get("requirement") or binding.get("requirement") or {}),
        "evidence_refs": evidence_refs,
        "intent": str(binding.get("intent") or "risk_review"),
        "status": str(binding.get("status") or "ready"),
        "continue_message": str(binding.get("continue_message") or ""),
        "selected_company_id": selected_company_id,
        "selected_supplier_name": selected_name if selected_company_id else None,
        "requires_identity_confirmation": requires_confirmation,
        "identity_candidates": identity_candidates,
        "consumed_turn_id": str(binding["consumed_turn_id"]) if binding.get("consumed_turn_id") else None,
        "run_id": str(binding["run_id"]) if binding.get("run_id") else None,
    }
    resolved = {**execution_context, "sourcing_handoff": handoff}
    if requires_confirmation:
        return resolved

    from app.graphs.agent_core import adapter

    reference = {
        "name": selected_name,
        "company_id": selected_company_id,
        "candidate_id": str(binding.get("candidate_key") or ""),
        "identity_status": "exact",
        "kind": "supplier",
        "source": "sourcing_handoff",
    }
    resolved["references"] = adapter.collect_supplier_references(
        list(resolved.get("references") or []), reference, "sourcing_handoff",
    )
    conversation_state = dict(resolved.get("conversation_state") or {})
    conversation_state["selected_supplier_names"] = [selected_name]
    conversation_state["selected_suppliers"] = [selected_name]
    current_task = dict(resolved.get("current_task") or {})
    current_task["target_supplier_names"] = [selected_name]
    current_task["selected_candidate_id"] = str(binding.get("candidate_key") or "")
    current_task["task_type"] = "analysis" if handoff["intent"] == "risk_review" else "action_draft"
    current_task["capability"] = "risk" if handoff["intent"] == "risk_review" else "monitoring"
    current_task["analysis_dimensions"] = ["risk"] if handoff["intent"] == "risk_review" else []
    if handoff["intent"] == "monitor_proposal":
        current_task["requested_action"] = "add_to_watchlist"
    conversation_state["current_task"] = current_task
    resolved["conversation_state"] = conversation_state
    resolved["current_task"] = current_task
    llm_intent = dict(resolved.get("llm_intent") or {})
    llm_intent["target_supplier_names"] = [selected_name]
    llm_intent["task_type"] = "analysis" if handoff["intent"] == "risk_review" else "action_draft"
    llm_intent["capability"] = "risk" if handoff["intent"] == "risk_review" else "monitoring"
    if handoff["intent"] == "monitor_proposal":
        llm_intent["requested_action"] = "add_to_watchlist"
    resolved["llm_intent"] = llm_intent
    return resolved


def can_access_handoff_candidate(
    company_id: str, supplier_name: str, user_id: str, user_role: str,
) -> bool:
    """Recheck current formal-supplier access; external/unmapped identity is denied."""
    del company_id
    from app.domains.supplier.access import can_access_formal_supplier_name

    access = can_access_formal_supplier_name(supplier_name, user_id, user_role)
    return access is True


def _source_owned_by(source: dict[str, Any], user_id: str) -> bool:
    return str((source.get("run") or {}).get("user_id") or "") == user_id


def _creation_response(binding: dict[str, Any]) -> dict[str, Any]:
    handoff_id = str(binding.get("handoff_id") or binding.get("id") or "")
    session_id = str(binding.get("session_id") or "")
    return {
        "handoff_id": handoff_id,
        "session_id": session_id,
        "chat_url": f"/chat?handoff_id={handoff_id}&session_id={session_id}",
    }


def _public_detail(binding: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(binding.get("candidate") or binding.get("candidate_snapshot") or {})
    requirement = dict(binding.get("requirement") or binding.get("requirement_snapshot") or {})
    source_policy = binding.get("source_policy") or {}
    return {
        "handoff_id": str(binding.get("handoff_id") or binding.get("id") or ""),
        "session_id": str(binding.get("session_id") or ""),
        "source_run_id": str(binding.get("source_run_id") or ""),
        "source_run_version": int(binding.get("source_run_version") or 1),
        "candidate_key": str(binding.get("candidate_key") or ""),
        "candidate": candidate,
        "requirement": requirement,
        "evidence_refs": _evidence_references(
            binding.get("evidence_refs") or [],
            freshness_days=source_policy.get("freshness_days") if isinstance(source_policy, dict) else None,
        ),
        "intent": str(binding.get("intent") or "risk_review"),
        "status": str(binding.get("status") or "ready"),
        "continue_message": str(binding.get("continue_message") or ""),
        "consumed_turn_id": str(binding["consumed_turn_id"]) if binding.get("consumed_turn_id") else None,
        "run_id": str(binding["run_id"]) if binding.get("run_id") else None,
    }


def _candidate_snapshot(candidate: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "candidate_key", "candidate_id", "company_id", "supplier_name", "name", "source",
        "status", "identity_status", "identity_candidates", "verification_status", "categories",
    }
    return {key: value for key, value in candidate.items() if key in allowed and value is not None}


def _identity_candidates(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    rows = candidate.get("identity_candidates") or []
    return [
        {
            "company_id": str(row.get("company_id") or ""),
            "supplier_name": str(row.get("supplier_name") or row.get("legal_name") or "").strip(),
            "supplier_id": str(row.get("supplier_id") or row.get("company_id") or ""),
            "short_name": row.get("short_name"),
        }
        for row in rows
        if isinstance(row, dict) and row.get("company_id") and (row.get("supplier_name") or row.get("legal_name"))
    ]


def _confirmed_identity(source: dict[str, Any], user_message: str) -> tuple[str | None, str]:
    candidate = source.get("candidate") or {}
    disposition = source.get("disposition") or {}
    company_id = candidate.get("company_id")
    if str(disposition.get("action") or "") == "confirm" and disposition.get("company_id"):
        company_id = disposition["company_id"]
    name = str(candidate.get("supplier_name") or candidate.get("name") or "").strip()
    if str(disposition.get("action") or "") == "confirm" and company_id:
        selected_option = next(
            (
                option for option in _identity_candidates(candidate)
                if option["company_id"] == str(company_id)
            ),
            None,
        )
        if selected_option:
            return str(company_id), selected_option["supplier_name"]
        return str(company_id), str(disposition.get("supplier_name") or name)
    if company_id and str(candidate.get("identity_status") or "") == "exact":
        return str(company_id), name

    message = str(user_message or "")
    matches = [
        option for option in _identity_candidates(candidate)
        if option["supplier_name"] and option["supplier_name"] in message
    ]
    if len(matches) == 1:
        return matches[0]["company_id"], matches[0]["supplier_name"]
    return None, name


def _evidence_references(
    items: list[dict[str, Any]], *, freshness_days: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from app.domains.sourcing_risk.evidence_service import (
        _freshness_status,
        _parse_timestamp,
        freshness_window_days,
    )

    refs: list[dict[str, Any]] = []
    checked_at = datetime.now(timezone.utc)
    for item in items:
        if not isinstance(item, dict):
            continue
        recorded_freshness = str(item.get("freshness_status") or "unknown").lower()
        dimension = str(item.get("dimension") or "")
        freshness = (
            "stale"
            if recorded_freshness == "stale"
            else _freshness_status(
                _parse_timestamp(item.get("observed_at")),
                checked_at,
                freshness_window_days(freshness_days, dimension),
            )
        )
        refs.append({
            key: item[key]
            for key in (
                "evidence_id", "dimension", "source_type", "source_reference", "observed_at", "collected_at",
                "claim_code",
            )
            if item.get(key) is not None
        } | {"freshness_status": freshness, "use": "current" if freshness == "fresh" else "historical"})
    return refs
