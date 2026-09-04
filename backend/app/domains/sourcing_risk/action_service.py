"""Approved, idempotent business actions for sourcing-risk V2 runs."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
import json
from typing import Any

from pymongo import ReturnDocument

from app.core.errors import DomainError
from app.core.config import settings
from app.core.rollout_gate import require_v2_execution
from app.db.mongo import get_db
from app.db.postgres import PgCursor, get_cursor
from app.domains.agent_run.repo import (
    append_event,
    get_run,
    get_run_for_user,
    insert_action_proposal,
    insert_approval_decision,
    update_run_status,
)
from app.domains.agent_run.schemas import ApprovalDecisionRequest
from app.domains.outbox.repo import enqueue_event

ACTION_EVENT_TYPE = "agent.action.approved"
ACTION_CONSUMER_NAME = "sourcing_risk_action"
SUPPORTED_ACTION_TYPES = frozenset(
    {
        "import_external_supplier",
        "add_watchlist",
        "remove_watchlist",
        "submit_access_application",
        "export_report",
    }
)


def create_action_proposal(
    run_id: str,
    action_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    candidate_id: str | None = None,
    *,
    user_id: str,
    user_role: str,
    expected_version: int,
) -> dict[str, Any]:
    """Persist a proposed write only; dispatch is deliberately approval-gated."""
    require_v2_execution(settings)
    _require_action_type(action_type)
    normalized_key = _require_idempotency_key(idempotency_key)
    with get_cursor() as (_, cur):
        run = get_run_for_update(cur, run_id)
        if run is None:
            raise DomainError("AGENT_RUN_NOT_FOUND", "任务不存在", 404)
        _require_proposal_creator(run, user_id, user_role)
        _require_expected_version(run, expected_version)
        persisted_payload = _bind_action_target(
            cur, run_id, action_type, candidate_id, payload
        )
        existing = get_action_proposal_for_idempotency_key(cur, normalized_key)
        if existing is not None:
            if not _is_same_proposal_request(
                existing, run_id, action_type, candidate_id, persisted_payload
            ):
                raise DomainError("AGENT_ACTION_IDEMPOTENCY_CONFLICT", "幂等键已用于其他操作", 409)
            return existing
        proposal = insert_action_proposal(
            run_id=run_id,
            action_type=action_type,
            payload=persisted_payload,
            idempotency_key=normalized_key,
            candidate_id=candidate_id,
            cur=cur,
        )
        if proposal is None:
            existing = get_action_proposal_for_idempotency_key(cur, normalized_key)
            if existing is None or not _is_same_proposal_request(
                existing, run_id, action_type, candidate_id, persisted_payload
            ):
                raise DomainError("AGENT_ACTION_IDEMPOTENCY_CONFLICT", "幂等键已用于其他操作", 409)
            return existing
    return proposal


def decide_action_proposal(
    run_id: str,
    proposal_id: str,
    request: ApprovalDecisionRequest,
    user_id: str,
    user_role: str,
) -> dict[str, Any]:
    """Approve/reject exactly one proposal and enqueue only an approved action."""
    require_v2_execution(settings)
    _require_approval_role(user_role)
    run = _get_authorized_run(run_id, user_id, user_role)
    _require_expected_version(run, request.expected_version)

    with get_cursor() as (_, cur):
        proposal = get_action_proposal_for_update(cur, run_id, proposal_id)
        if proposal is None:
            raise DomainError("AGENT_ACTION_PROPOSAL_NOT_FOUND", "操作提案不存在", 404)
        if proposal["run_id"] != run_id:
            raise DomainError("AGENT_ACTION_PROPOSAL_NOT_FOUND", "操作提案不存在", 404)
        if proposal["status"] != "pending":
            raise DomainError("AGENT_ACTION_ALREADY_DECIDED", "操作提案已处理", 409)
        if run["status"] != "ACTION_PENDING":
            raise DomainError("AGENT_RUN_INVALID_STATE", "任务当前状态不允许审批操作", 409)
        if request.decision == "approved":
            _verify_harness_approval(proposal, run, request.approval_token, user_id)
            _require_non_self_approval_for_high_risk_import(proposal, user_id, run)

        target_status = "ACTION_EXECUTING" if request.decision == "approved" else "READY_FOR_REVIEW"
        updated_run = update_run_status(
            run_id,
            request.expected_version,
            target_status,
            cur=cur,
        )
        if updated_run is None:
            raise DomainError("AGENT_RUN_VERSION_CONFLICT", "任务版本已变更", 409)
        updated_proposal = update_action_proposal(
            cur,
            run_id,
            proposal_id,
            status=request.decision,
            execution_state="pending" if request.decision == "approved" else "rejected",
        )
        decision = insert_approval_decision(
            run_id,
            proposal_id,
            request.decision,
            user_id,
            request.comment,
            cur=cur,
        )
        append_event(
            run_id,
            updated_run["version"],
            "approval",
            {"approval_id": proposal_id, "decision": request.decision, "status": updated_run["status"]},
            cur=cur,
        )
        if request.decision == "approved":
            enqueue_event(
                cur,
                ACTION_EVENT_TYPE,
                "agent_action_proposal",
                proposal_id,
                {
                    "run_id": run_id,
                    "proposal_id": proposal_id,
                    "idempotency_key": proposal["idempotency_key"],
                    "approver_id": user_id,
                },
            )
    return {"run": updated_run, "proposal": updated_proposal, "approval": decision}


def decide_action_proposals(
    run_id: str,
    proposal_ids: list[str],
    request: ApprovalDecisionRequest,
    user_id: str,
    user_role: str,
) -> list[dict[str, Any]]:
    """Approve an aggregate of proposals while advancing the run once."""
    require_v2_execution(settings)
    _require_approval_role(user_role)
    if not proposal_ids:
        return []
    run = _get_authorized_run(run_id, user_id, user_role)
    _require_expected_version(run, request.expected_version)

    with get_cursor() as (_, cur):
        locked_run = get_run_for_update(cur, run_id)
        if locked_run is None:
            raise DomainError("AGENT_ACTION_APPROVAL_FORBIDDEN", "没有审批该操作的权限", 403)
        _require_expected_version(locked_run, request.expected_version)
        proposals = []
        for proposal_id in proposal_ids:
            proposal = get_action_proposal_for_update(cur, run_id, proposal_id)
            if proposal is None or proposal["run_id"] != run_id:
                raise DomainError("AGENT_ACTION_PROPOSAL_NOT_FOUND", "操作提案不存在", 404)
            if proposal["status"] != "pending":
                raise DomainError("AGENT_ACTION_ALREADY_DECIDED", "操作提案已处理", 409)
            if request.decision == "approved":
                _verify_harness_approval(proposal, locked_run, request.approval_token, user_id)
                _require_non_self_approval_for_high_risk_import(proposal, user_id, locked_run)
            proposals.append(proposal)
        if locked_run["status"] != "ACTION_PENDING":
            raise DomainError("AGENT_RUN_INVALID_STATE", "任务当前状态不允许审批操作", 409)

        target_status = "ACTION_EXECUTING" if request.decision == "approved" else "READY_FOR_REVIEW"
        updated_run = update_run_status(
            run_id, request.expected_version, target_status, cur=cur
        )
        if updated_run is None:
            raise DomainError("AGENT_RUN_VERSION_CONFLICT", "任务版本已变更", 409)

        decisions: list[dict[str, Any]] = []
        for proposal in proposals:
            proposal_id = str(proposal["id"])
            updated_proposal = update_action_proposal(
                cur,
                run_id,
                proposal_id,
                status=request.decision,
                execution_state="pending" if request.decision == "approved" else "rejected",
            )
            decision = insert_approval_decision(
                run_id, proposal_id, request.decision, user_id, request.comment, cur=cur
            )
            append_event(
                run_id,
                updated_run["version"],
                "approval",
                {"approval_id": proposal_id, "decision": request.decision, "status": updated_run["status"]},
                cur=cur,
            )
            if request.decision == "approved":
                enqueue_event(
                    cur,
                    ACTION_EVENT_TYPE,
                    "agent_action_proposal",
                    proposal_id,
                    {
                        "run_id": run_id,
                        "proposal_id": proposal_id,
                        "idempotency_key": proposal["idempotency_key"],
                        "approver_id": user_id,
                    },
                )
            decisions.append({"run": updated_run, "proposal": updated_proposal, "approval": decision})
    return decisions


def execute_sourcing_risk_action(event: dict) -> None:
    """Run an approved action once; every adapter owns its durable idempotency key."""
    require_v2_execution(settings)
    payload = dict(event.get("payload") or {})
    run_id = _required_event_value(payload, "run_id")
    proposal_id = _required_event_value(payload, "proposal_id")
    proposal = get_action_proposal_for_execution(run_id, proposal_id)
    if proposal is None:
        raise DomainError("AGENT_ACTION_PROPOSAL_NOT_FOUND", "操作提案不存在", 404)
    if proposal["execution_state"] == "succeeded":
        return
    if proposal["status"] != "approved":
        raise DomainError("AGENT_ACTION_NOT_APPROVED", "操作尚未批准", 409)

    action_payload = {**dict(proposal["payload"]), "idempotency_key": proposal["idempotency_key"]}
    if proposal["action_type"] in {"add_watchlist", "remove_watchlist"}:
        _execute_watchlist_action(event, proposal, action_payload)
    else:
        _execute_action(proposal["action_type"], action_payload)
    mark_action_succeeded(run_id, proposal_id)


def import_external_supplier(payload: dict[str, Any]) -> str:
    """Explicit supplier-master import adapter; it never resolves or auto-creates by lookup."""
    company_name = _required_event_value(payload, "company_name")
    idempotency_key = _require_idempotency_key(_required_event_value(payload, "idempotency_key"))
    document = {
        "name": company_name,
        "unified_code": payload.get("unified_code"),
        "legal_person": payload.get("legal_person"),
        "categories": list(payload.get("categories") or []),
        "regions": list(payload.get("regions") or []),
        "status": payload.get("supplier_status", "prospective"),
        "source": "sourcing_risk_v2",
        "agent_action_key": idempotency_key,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    db = get_db()
    supplier_id = str(uuid.uuid4())
    document["_id"] = supplier_id
    persisted = db["suppliers"].find_one_and_update(
        {"agent_action_key": idempotency_key},
        {"$setOnInsert": document},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return str(persisted["_id"])


def add_watchlist(payload: dict[str, Any]) -> None:
    from app.domains.alert.service import add_to_watchlist as add_to_watchlist_service

    add_to_watchlist_service(_required_event_value(payload, "company_name"))


def remove_watchlist(payload: dict[str, Any]) -> None:
    from app.domains.alert.service import remove_from_watchlist as remove_from_watchlist_service

    remove_from_watchlist_service(_required_event_value(payload, "company_name"))


def submit_access_application(payload: dict[str, Any]) -> str:
    """Create an access request without legacy supplier auto-creation."""
    company_name = _required_event_value(payload, "company_name")
    key = _require_idempotency_key(_required_event_value(payload, "idempotency_key"))
    db = get_db()
    application_id = str(uuid.uuid4())
    persisted = db["access_applications"].find_one_and_update(
        {"agent_action_key": key},
        {"$setOnInsert": {
            "_id": application_id,
            "supplier_name": company_name,
            "supplier_id": payload.get("supplier_id"),
            "request_id": payload.get("request_id"),
            "applicant_id": _required_event_value(payload, "applicant_id"),
            "status": "pending",
            "agent_action_key": key,
            "created_at": datetime.now(timezone.utc),
        }},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return str(persisted["_id"])


def export_report(payload: dict[str, Any]) -> str:
    """Persist an idempotent export request for the report delivery adapter."""
    key = _require_idempotency_key(_required_event_value(payload, "idempotency_key"))
    db = get_db()
    db["agent_report_exports"].update_one(
        {"agent_action_key": key},
        {"$setOnInsert": {"agent_action_key": key, "payload": dict(payload), "created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return key


def record_action_delivery_outcome(event: dict, outcome: str) -> None:
    """Persist retry/dead-letter status after the outbox transaction has won its lease."""
    payload = dict(event.get("payload") or {})
    run_id = payload.get("run_id")
    proposal_id = payload.get("proposal_id")
    if not isinstance(run_id, str) or not isinstance(proposal_id, str):
        return
    if outcome not in {"retry", "dead_lettered"}:
        return
    if outcome == "retry":
        append_action_status(run_id, proposal_id, "retry")
        return
    mark_action_dead_lettered(run_id, proposal_id)
    _mark_run_action_failed_after_dead_letter(run_id)


def get_action_proposal_for_update(cur: PgCursor, run_id: str, proposal_id: str) -> dict[str, Any] | None:
    cur.execute(
        "SELECT * FROM agent_action_proposals WHERE run_id = %s AND id = %s FOR UPDATE",
        (run_id, proposal_id),
    )
    return _row_to_dict(cur, cur.fetchone())


def get_run_for_update(cur: PgCursor, run_id: str) -> dict[str, Any] | None:
    cur.execute("SELECT * FROM agent_runs WHERE id = %s FOR UPDATE", (run_id,))
    return _row_to_dict(cur, cur.fetchone())


def get_action_proposal_for_idempotency_key(cur: PgCursor, idempotency_key: str) -> dict[str, Any] | None:
    cur.execute(
        "SELECT * FROM agent_action_proposals WHERE idempotency_key = %s FOR UPDATE",
        (idempotency_key,),
    )
    return _row_to_dict(cur, cur.fetchone())


def get_action_candidate_for_update(
    cur: PgCursor, candidate_id: str
) -> dict[str, Any] | None:
    cur.execute(
        "SELECT * FROM agent_run_candidates WHERE id = %s FOR UPDATE", (candidate_id,)
    )
    return _row_to_dict(cur, cur.fetchone())


def _bind_action_target(
    cur: PgCursor,
    run_id: str,
    action_type: str,
    candidate_id: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Bind every writable target to this Run before it becomes approvable."""
    if action_type == "import_external_supplier":
        if candidate_id is None:
            raise DomainError("AGENT_ACTION_CANDIDATE_REQUIRED", "外部导入必须指定候选企业", 422)
        candidate = get_action_candidate_for_update(cur, candidate_id)
        if candidate is None or candidate["run_id"] != run_id:
            raise DomainError("AGENT_ACTION_CANDIDATE_NOT_FOUND", "候选企业不属于任务", 404)
        if candidate["source"] not in {"staged_external", "external"} or candidate["status"] != "staged_candidate":
            raise DomainError("AGENT_ACTION_CANDIDATE_INVALID", "候选企业不是可导入的外部暂存候选", 422)
        snapshot = candidate.get("candidate_snapshot")
        if not isinstance(snapshot, dict):
            raise DomainError("AGENT_ACTION_CANDIDATE_INVALID", "候选企业快照无效", 422)
        return dict(snapshot)

    if action_type in {"add_watchlist", "remove_watchlist"} and candidate_id is None:
        company_name = payload.get("company_name")
        if (
            not isinstance(company_name, str)
            or not company_name.strip()
            or payload.get("target_source") != "conversation_state"
        ):
            raise DomainError("AGENT_ACTION_TARGET_REQUIRED", "加入监控必须绑定当前任务企业", 422)
        return dict(payload)

    if action_type == "submit_access_application" and candidate_id is None and payload.get("company_id") is None:
        raise DomainError("AGENT_ACTION_TARGET_REQUIRED", "操作必须绑定当前任务企业或候选企业", 422)

    if candidate_id is not None:
        candidate = get_action_candidate_for_update(cur, candidate_id)
        if candidate is None or candidate["run_id"] != run_id:
            raise DomainError("AGENT_ACTION_CANDIDATE_NOT_FOUND", "候选企业不属于任务", 404)
        candidate_company_id = candidate.get("company_id")
        if candidate_company_id is not None and payload.get("company_id") not in {None, candidate_company_id}:
            raise DomainError("AGENT_ACTION_COMPANY_INVALID", "企业标识与候选企业不一致", 422)
        if candidate_company_id is not None:
            return {**dict(payload), "company_id": candidate_company_id}
    company_id = payload.get("company_id")
    if company_id is not None:
        if not isinstance(company_id, str) or not company_id.strip():
            raise DomainError("AGENT_ACTION_COMPANY_INVALID", "企业标识无效", 422)
        cur.execute(
            "SELECT 1 FROM agent_run_candidates WHERE company_id = %s AND run_id = %s",
            (company_id, run_id),
        )
        if cur.fetchone() is None:
            raise DomainError("AGENT_ACTION_COMPANY_NOT_FOUND", "企业不属于任务", 404)
    return dict(payload)


def _is_same_proposal_request(
    existing: dict[str, Any],
    run_id: str,
    action_type: str,
    candidate_id: str | None,
    payload: dict[str, Any],
) -> bool:
    return (
        existing["run_id"] == run_id
        and existing["action_type"] == action_type
        and existing.get("candidate_id") == candidate_id
        and _canonical_payload(existing.get("payload") or {}) == _canonical_payload(payload)
    )


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def get_action_proposal_for_execution(run_id: str, proposal_id: str) -> dict[str, Any] | None:
    with get_cursor() as (_, cur):
        return get_action_proposal_for_update(cur, run_id, proposal_id)


def update_action_proposal(
    cur: PgCursor,
    run_id: str,
    proposal_id: str,
    *,
    status: str | None = None,
    execution_state: str | None = None,
) -> dict[str, Any]:
    cur.execute(
        """
        UPDATE agent_action_proposals
        SET status = COALESCE(%s, status),
            execution_state = COALESCE(%s, execution_state),
            updated_at = NOW()
        WHERE run_id = %s AND id = %s
        RETURNING *
        """,
        (status, execution_state, run_id, proposal_id),
    )
    proposal = _row_to_dict(cur, cur.fetchone())
    if proposal is None:
        raise DomainError("AGENT_ACTION_PROPOSAL_NOT_FOUND", "操作提案不存在", 404)
    return proposal


def mark_action_succeeded(run_id: str, proposal_id: str) -> None:
    with get_cursor() as (_, cur):
        update_action_proposal(cur, run_id, proposal_id, status="succeeded", execution_state="succeeded")
        _append_action_status_with_cursor(cur, run_id, proposal_id, "succeeded")


def mark_action_dead_lettered(run_id: str, proposal_id: str) -> None:
    with get_cursor() as (_, cur):
        update_action_proposal(cur, run_id, proposal_id, status="failed", execution_state="dead_lettered")
        _append_action_status_with_cursor(cur, run_id, proposal_id, "dead_lettered")


def append_action_status(run_id: str, proposal_id: str, outcome: str) -> None:
    with get_cursor() as (_, cur):
        _append_action_status_with_cursor(cur, run_id, proposal_id, outcome)


def _append_action_status_with_cursor(
    cur: PgCursor, run_id: str, proposal_id: str, outcome: str
) -> None:
    run = get_run_for_update(cur, run_id)
    if run is not None:
        append_event(
            run_id,
            run["version"],
            "action_status",
            {"proposal_id": proposal_id, "outcome": outcome},
            cur=cur,
        )


def _mark_run_action_failed_after_dead_letter(run_id: str) -> None:
    run = get_run(run_id)
    if run is None or run["status"] != "ACTION_EXECUTING":
        return
    with get_cursor() as (_, cur):
        updated = update_run_status(run_id, run["version"], "ACTION_FAILED", cur=cur)
        if updated is not None:
            append_event(
                run_id,
                updated["version"],
                "action_status",
                {"outcome": "dead_lettered", "status": updated["status"]},
                cur=cur,
            )


def _execute_action(action_type: str, payload: dict[str, Any]) -> None:
    _require_action_type(action_type)
    handlers = {
        "import_external_supplier": import_external_supplier,
        "add_watchlist": add_watchlist,
        "remove_watchlist": remove_watchlist,
        "submit_access_application": submit_access_application,
        "export_report": export_report,
    }
    handlers[action_type](payload)


def _execute_watchlist_action(
    event: dict[str, Any], proposal: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Execute monitor mutations through the shared Harness ToolExecutor boundary."""
    from app.graphs.harness.actions import issue_approval_token
    from app.graphs.harness.durable_actions import proposal_from_durable_row
    from app.tools.executor import ToolContext, ToolExecutor
    from app.tools import TOOL_REGISTRY

    event_payload = dict(event.get("payload") or {})
    run_id = _required_event_value(event_payload, "run_id")
    approver_id = _required_event_value(event_payload, "approver_id")
    run = get_run(run_id)
    if run is None:
        raise DomainError("AGENT_RUN_NOT_FOUND", "任务不存在", 404)
    requester_id = str(run.get("user_id") or "")
    session_id = str(run.get("session_id") or "")
    metadata = dict((proposal.get("payload") or {}).get("_harness_action") or {})
    session_id = session_id or str(metadata.get("session_id") or "")
    if not requester_id or not session_id:
        raise DomainError("AGENT_ACTION_EXECUTION_CONTEXT_INVALID", "执行上下文缺少用户或会话绑定", 409)
    try:
        harness_proposal = proposal_from_durable_row(
            proposal,
            session_id=session_id,
            user_id=requester_id,
        )
        token = issue_approval_token(
            harness_proposal,
            approver_id,
            secret_key=settings.SECRET_KEY,
        )
        context = ToolContext(
            call_id=str(uuid.uuid4()),
            session_id=session_id,
            run_id=run_id,
            user_id=requester_id,
            approval_token=token,
            approval_proposal_id=str(proposal["id"]),
            approval_actor_id=approver_id,
            approval_secret_key=settings.SECRET_KEY,
            idempotency_key=str(proposal["idempotency_key"]),
        )
        result = asyncio.run(
            ToolExecutor(TOOL_REGISTRY).execute(
                harness_proposal.tool_name, payload, context
            )
        )
    except (ValueError, TypeError) as exc:
        raise DomainError("AGENT_ACTION_EXECUTION_CONTEXT_INVALID", str(exc), 409) from exc
    if result.status != "success" or not result.side_effect_receipt:
        raise DomainError(
            "AGENT_ACTION_EXECUTION_FAILED",
            f"{harness_proposal.tool_name} 未返回有效副作用回执",
            502,
        )


def _get_authorized_run(run_id: str, user_id: str, user_role: str) -> dict[str, Any]:
    run = get_run(run_id) if user_role == "admin" else get_run_for_user(run_id, user_id)
    if run is None:
        raise DomainError("AGENT_ACTION_APPROVAL_FORBIDDEN", "没有审批该操作的权限", 403)
    return run


def _require_proposal_creator(run: dict[str, Any], user_id: str, user_role: str) -> None:
    if user_role not in {"admin", "analyst"}:
        raise DomainError("AGENT_ACTION_PROPOSAL_FORBIDDEN", "没有创建操作提案的权限", 403)
    if user_role != "admin" and run.get("user_id") != user_id:
        raise DomainError("AGENT_ACTION_PROPOSAL_FORBIDDEN", "没有创建该任务操作提案的权限", 403)


def _require_approval_role(user_role: str) -> None:
    if user_role not in {"admin", "analyst"}:
        raise DomainError("AGENT_ACTION_APPROVAL_FORBIDDEN", "没有审批权限", 403)


def _require_expected_version(run: dict[str, Any], expected_version: int) -> None:
    if run["version"] != expected_version:
        raise DomainError("AGENT_RUN_VERSION_CONFLICT", "任务版本已变更", 409)


def _require_non_self_approval_for_high_risk_import(
    proposal: dict[str, Any], user_id: str, run: dict[str, Any]
) -> None:
    payload = dict(proposal.get("payload") or {})
    high_risk = payload.get("risk_level") == "high" or int(payload.get("risk_score") or 0) >= 70
    if proposal["action_type"] == "import_external_supplier" and high_risk and run.get("user_id") == user_id:
        raise DomainError("AGENT_ACTION_SELF_APPROVAL_FORBIDDEN", "高风险导入不能由发起人审批", 403)


def _verify_harness_approval(
    proposal: dict[str, Any],
    run: dict[str, Any],
    approval_token: str | None,
    approver_id: str,
) -> None:
    """Verify Harness-bound approval metadata before the durable state transition."""
    metadata = dict((proposal.get("payload") or {}).get("_harness_action") or {})
    if not metadata:
        if proposal.get("action_type") in {"add_watchlist", "remove_watchlist"}:
            raise DomainError(
                "AGENT_ACTION_APPROVAL_CONTEXT_INVALID",
                "监控写操作缺少 Harness 动作绑定元数据，已拒绝审批",
                409,
            )
        # Keep the legacy V2 action compatibility surface intact for action
        # types that have not yet been migrated to a registered tool.
        return
    from app.graphs.harness.actions import issue_approval_token, verify_approval_token
    from app.graphs.harness.durable_actions import proposal_from_durable_row

    requester_id = str(run.get("user_id") or "")
    session_id = str(run.get("session_id") or metadata.get("session_id") or "")
    if not requester_id or not session_id:
        raise DomainError("AGENT_ACTION_APPROVAL_CONTEXT_INVALID", "审批上下文缺少用户或会话绑定", 409)
    try:
        harness_proposal = proposal_from_durable_row(
            proposal,
            session_id=session_id,
            user_id=requester_id,
        )
        # The server may issue the short-lived token at the moment the
        # authenticated human approves. A client-provided token is still
        # accepted for reconnect/recovery, but is always verified here.
        token = approval_token or issue_approval_token(
            harness_proposal,
            approver_id,
        )
        verify_approval_token(
            token,
            harness_proposal,
            approver_id=approver_id,
            secret_key=settings.SECRET_KEY,
        )
    except ValueError as exc:
        raise DomainError("AGENT_ACTION_APPROVAL_TOKEN_INVALID", str(exc), 409) from exc


def _require_action_type(action_type: str) -> None:
    if action_type not in SUPPORTED_ACTION_TYPES:
        raise DomainError("AGENT_ACTION_TYPE_INVALID", "操作类型无效", 422)


def _require_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise DomainError("AGENT_ACTION_IDEMPOTENCY_KEY_INVALID", "幂等键无效", 422)
    return normalized


def _required_event_value(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DomainError("AGENT_ACTION_EVENT_INVALID", "操作事件缺少必要字段", 422)
    return value.strip()


def _row_to_dict(cur: PgCursor, row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(zip((column[0] for column in cur.description), row))
    for key, value in result.items():
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
    return result
