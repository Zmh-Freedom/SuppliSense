"""Framework-independent lifecycle commands for sourcing-risk agent runs."""

import hashlib
import json
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.core.errors import DomainError
from app.db.postgres import get_cursor
from app.domains.sourcing_risk.evidence_service import (
    RawPayloadStagingError,
    commit_raw_payloads,
    compensate_raw_payloads,
    get_raw_payload_lifecycle_statuses,
    retry_raw_payload_compensations,
    stage_raw_payloads,
)
from app.domains.agent_run.models import ALLOWED_STATUS_TRANSITIONS, AgentRunStatus
from app.domains.agent_run.harness_contracts import TurnStatus
from app.domains.agent_run.repo import (
    append_event,
    cancel_pending_candidate_review_batches_with_cursor,
    get_candidate_review_event_by_request_with_cursor,
    get_candidate_followup_event_by_request_with_cursor,
    get_candidate_followup_context,
    get_candidate_review_batch_for_execution as repo_get_candidate_review_batch_for_execution,
    get_policy_snapshot_by_id_with_cursor,
    get_policy_snapshot_by_run_with_cursor,
    get_latest_harness_run_for_session,
    get_run,
    get_run_for_update,
    get_run_detail_collections,
    get_run_for_user,
    insert_candidate_review_event_with_cursor,
    insert_candidate_followup_event_with_cursor,
    insert_candidate_followup_with_cursor,
    insert_approval_decision,
    insert_run,
    list_candidate_review_events_with_cursor,
    list_candidates_with_cursor,
    list_events_after,
    list_execution_snapshot_events,
    list_raw_payload_compensations,
    list_sourcing_candidate_dispositions_with_cursor,
    lock_candidate_review_request_with_cursor,
    lock_candidate_followup_request_with_cursor,
    mark_candidate_review_batch_consumed_with_cursor,
    persist_run_snapshot,
    update_raw_payload_compensation,
    update_run_requirement,
    update_run_policy_snapshot_with_cursor,
    update_run_status,
    touch_terminal_run_with_cursor,
    upsert_raw_payload_compensations,
    upsert_sourcing_candidate_disposition_with_cursor,
    upsert_policy_snapshot_with_cursor,
)
from app.domains.agent_run.schemas import (
    AgentRunResponse,
    ApprovalDecisionRequest,
    AgentExecutionSnapshot,
    CandidateDecisionBatchRequest,
    ClarificationRequest,
    CreateSourcingRiskRunRequest,
    IdentityResolutionRequest,
)
from app.domains.sourcing.candidate_review import apply_candidate_decisions
from app.graphs.agent_core.trace import TRACE_EVENT_TYPE, build_agent_trace_events

TERMINAL_STATUSES = frozenset(
    {
        AgentRunStatus.COMPLETED.value,
        AgentRunStatus.PARTIAL.value,
        AgentRunStatus.NEEDS_REVIEW.value,
        AgentRunStatus.ACTION_FAILED.value,
        AgentRunStatus.FAILED.value,
        AgentRunStatus.CANCELLED.value,
        AgentRunStatus.ROLLBACK_FROZEN.value,
    }
)

SUPERVISOR_APPROVAL_TTL = timedelta(hours=24)
_SUPERVISOR_ACTION_TYPES = {
    "add_to_watchlist": "add_watchlist",
}
_SUPERVISOR_DURABLE_STATUSES = {
    "WAITING_HUMAN_APPROVAL": AgentRunStatus.ACTION_PENDING.value,
    "COMPLETED": AgentRunStatus.COMPLETED.value,
    "PARTIAL_COMPLETED": AgentRunStatus.PARTIAL.value,
    "FAILED": AgentRunStatus.FAILED.value,
}


def can_read_all_agent_runs(user_role: str) -> bool:
    """Only administrators may bypass creator scoping for operational recovery."""
    return user_role == "admin"


def create_sourcing_risk_run(
    request: CreateSourcingRiskRunRequest,
    user_id: str,
    user_role: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    del user_role
    with get_cursor() as (_, cur):
        run = insert_run(
            run_type="sourcing_risk_v2",
            requirement=request.model_dump(),
            user_id=user_id,
            cur=cur,
            session_id=session_id,
        )
        append_event(run["id"], run["version"], "stage", {"status": run["status"]}, cur=cur)
    return run


def persist_parsed_requirement(run_id: str, parsed_requirement: dict[str, Any]) -> dict[str, Any] | None:
    """Persist the validated requirement that the graph uses for discovery.

    A newly created run stores the user's raw input first. The graph then adds
    deterministic/validated fields before discovery; keeping that normalized
    snapshot on the run row makes GET/SSE recovery describe the same scope the
    graph actually executed.
    """
    run = get_run(run_id)
    if run is None:
        return None
    current_requirement = dict(run.get("requirement") or {})
    normalized_requirement = {**current_requirement, **dict(parsed_requirement)}
    if normalized_requirement == current_requirement:
        return run
    updated = update_run_requirement(
        run_id,
        run["version"],
        normalized_requirement,
        run["status"],
    )
    if updated is None:
        _raise_version_conflict()
    return updated


def get_sourcing_risk_run(run_id: str, user_id: str, user_role: str) -> dict[str, Any]:
    run = _get_authorized_run(run_id, user_id, user_role)
    detail = get_run_detail_collections(run_id)
    compensations = get_raw_payload_compensations(run_id)
    raw_payload_refs = _raw_payload_refs(detail["evidence_by_company_id"])
    try:
        mongo_statuses = get_raw_payload_lifecycle_statuses(raw_payload_refs)
    except Exception as exc:
        raise DomainError(
            "AGENT_RUN_RECOVERY_UNAVAILABLE", "原始证据恢复状态不可用，请稍后重试", 503
        ) from exc
    raw_payload_statuses = _merge_compensation_statuses(mongo_statuses, compensations)
    detail = _fail_closed_for_raw_payload_recovery(detail, raw_payload_statuses)
    response = AgentRunResponse(
        id=run["id"],
        run_id=run["id"],
        status=run["status"],
        version=run["version"],
        requirement=run["requirement"],
        candidates=detail["candidates"],
        evidence_by_company_id=detail["evidence_by_company_id"],
        evidence_reviews=detail["evidence_reviews"],
        decisions=detail["decisions"],
        action_proposals=detail["action_proposals"],
        approvals=detail["approvals"],
    ).model_dump(mode="json")
    return {
        **run,
        **response,
        "id": run["id"],
        "proposals": response["action_proposals"],
        "raw_payload_statuses": raw_payload_statuses,
    }


def retry_sourcing_risk_raw_payload_compensations(
    run_id: str, user_id: str, user_role: str
) -> list[dict[str, str]]:
    """Retry only the raw payload cleanups referenced by an authorized Run's evidence."""
    _get_authorized_run(run_id, user_id, user_role)
    detail = get_run_detail_collections(run_id)
    compensations = get_raw_payload_compensations(run_id)
    refs = _raw_payload_refs(detail["evidence_by_company_id"], include_missing=False)
    refs.extend(
        item["raw_payload_ref"]
        for item in compensations
        if not _is_synthetic_raw_payload_ref(item["raw_payload_ref"])
    )
    staging_owners = {
        item["raw_payload_ref"]: item["staging_owner"]
        for item in compensations
        if item.get("staging_owner")
    }
    try:
        outcomes = (
            retry_raw_payload_compensations(refs, staging_owners=staging_owners)
            if staging_owners
            else retry_raw_payload_compensations(refs)
        )
    except Exception as exc:
        raise DomainError(
            "AGENT_RUN_RECOVERY_UNAVAILABLE", "原始证据补偿重试不可用，请稍后重试", 503
        ) from exc
    compensation_refs = {item["raw_payload_ref"] for item in compensations}
    compensated_refs = {
        item["raw_payload_ref"]
        for item in compensations
        if str(item.get("status") or item.get("lifecycle_status")) == "compensated"
    }
    stable_outcomes: list[dict[str, str]] = []
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        raw_payload_ref = outcome["raw_payload_ref"]
        if (
            raw_payload_ref in compensated_refs
            and outcome.get("lifecycle_status") == "compensated"
        ):
            stable_outcomes.append(
                {"raw_payload_ref": raw_payload_ref, "lifecycle_status": "compensated"}
            )
            continue
        if raw_payload_ref in compensation_refs:
            update_raw_payload_compensation(run_id, raw_payload_ref, outcome["lifecycle_status"])
        elif outcome.get("lifecycle_status") == "unknown":
            _record_compensations(
                [{
                    "run_id": run_id,
                    "raw_payload_ref": raw_payload_ref,
                    "staging_owner": f"recovery-{uuid4()}",
                }],
                "mongo_recovery_state_unknown",
            )
        stable_outcomes.append(outcome)
    return stable_outcomes


def get_raw_payload_compensations(run_id: str) -> list[dict[str, Any]]:
    try:
        return list_raw_payload_compensations(run_id)
    except Exception as exc:
        raise DomainError(
            "AGENT_RUN_RECOVERY_UNAVAILABLE", "原始证据补偿索引不可用，请稍后重试", 503
        ) from exc


def _fail_closed_for_raw_payload_recovery(
    detail: dict[str, Any], statuses: list[dict[str, str]]
) -> dict[str, Any]:
    safe = {"committed", "compensated"}
    if statuses and all(item.get("lifecycle_status") in safe for item in statuses):
        return detail
    if not statuses:
        return detail
    reason = "RAW_PAYLOAD_RECOVERY_REQUIRED"
    return {
        **detail,
        "candidates": [
            {**candidate, "score_eligible": False, "recovery_required": True}
            for candidate in detail["candidates"]
        ],
        "decisions": [
            {
                **decision,
                "score_eligible": False,
                "recovery_required": True,
                "reason_codes": list(dict.fromkeys([*(decision.get("reason_codes") or []), reason])),
            }
            for decision in detail["decisions"]
        ],
    }


def _merge_compensation_statuses(
    mongo_statuses: list[dict[str, str]], compensations: list[dict[str, Any]]
) -> list[dict[str, str]]:
    mongo_by_ref = {
        item["raw_payload_ref"]: _normalize_mongo_lifecycle_status(item.get("lifecycle_status"))
        for item in mongo_statuses
    }
    merged = {
        item["raw_payload_ref"]: {
            "raw_payload_ref": item["raw_payload_ref"],
            "lifecycle_status": mongo_status,
        }
        for item in mongo_statuses
        for mongo_status in [_normalize_mongo_lifecycle_status(item.get("lifecycle_status"))]
    }
    for item in compensations:
        raw_payload_ref = item["raw_payload_ref"]
        compensation_status = _normalize_pg_recovery_status(
            item.get("status") or item.get("lifecycle_status") or "pending_compensation"
        )
        mongo_status = mongo_by_ref.get(raw_payload_ref)
        if mongo_status is None:
            # A recovery row without a Mongo observation is not proof of cleanup.
            lifecycle_status = (
                "pending_compensation"
                if compensation_status == "pending_compensation"
                else "unknown"
            )
        elif mongo_status in _SAFE_RAW_PAYLOAD_STATUSES and compensation_status in _SAFE_RAW_PAYLOAD_STATUSES:
            lifecycle_status = (
                "compensated"
                if "compensated" in {mongo_status, compensation_status}
                else "committed"
            )
        elif mongo_status in {"pending_compensation", "orphan", "unknown"}:
            lifecycle_status = mongo_status
        elif compensation_status in {"pending_compensation", "unknown"}:
            lifecycle_status = compensation_status
        else:
            lifecycle_status = mongo_status
        merged[raw_payload_ref] = {"raw_payload_ref": raw_payload_ref, "lifecycle_status": lifecycle_status}
    return list(merged.values())


_SAFE_RAW_PAYLOAD_STATUSES = frozenset({"committed", "compensated"})


def _normalize_mongo_lifecycle_status(value: object) -> str:
    status = str(value or "unknown")
    return status if status in {"pending", "pending_compensation", "committed", "compensated", "orphan", "unknown"} else "unknown"


def _normalize_pg_recovery_status(value: object) -> str:
    status = str(value or "unknown")
    return status if status in {"pending_compensation", "committed", "compensated"} else "unknown"


def _raw_payload_refs(
    evidence_by_company_id: dict[str, list[dict[str, Any]]], *, include_missing: bool = True
) -> list[str]:
    refs: list[str] = []
    for company_id, evidence_items in evidence_by_company_id.items():
        for evidence in evidence_items:
            raw_payload_ref = evidence.get("raw_payload_ref")
            if not raw_payload_ref and not include_missing:
                continue
            raw_payload_ref = raw_payload_ref or _missing_raw_payload_ref(company_id, evidence)
            if raw_payload_ref not in refs:
                refs.append(str(raw_payload_ref))
    return refs


def _is_synthetic_raw_payload_ref(raw_payload_ref: object) -> bool:
    return str(raw_payload_ref).startswith("missing:")


def _missing_raw_payload_ref(company_id: str, evidence: dict[str, Any]) -> str:
    identity = evidence.get("evidence_id") or evidence.get("source_reference") or evidence.get("dimension")
    return f"missing:{company_id}:{identity or 'unknown'}"


def _get_authorized_run(run_id: str, user_id: str, user_role: str) -> dict[str, Any]:
    run = get_run(run_id) if can_read_all_agent_runs(user_role) else get_run_for_user(run_id, user_id)
    if run is None:
        raise DomainError("AGENT_RUN_NOT_FOUND", "任务不存在", 404)
    return run


def submit_clarification(
    run_id: str,
    request: ClarificationRequest,
    user_id: str,
    user_role: str,
) -> dict[str, Any]:
    run = _get_authorized_run(run_id, user_id, user_role)
    _require_version(run, request.expected_version)
    _require_transition(run, AgentRunStatus.CREATED)
    requirement = {**dict(run.get("requirement") or {}), **dict(request.answers)}
    with get_cursor() as (_, cur):
        updated = update_run_requirement(
            run_id,
            request.expected_version,
            requirement,
            AgentRunStatus.CREATED.value,
            cur=cur,
        )
        if updated is None:
            _raise_version_conflict()
        append_event(
            run_id, updated["version"], "clarification",
            {"answers": dict(request.answers), "status": updated["status"]}, cur=cur,
        )
    return updated


def submit_identity_resolution(
    run_id: str,
    request: IdentityResolutionRequest,
    user_id: str,
    user_role: str,
) -> dict[str, Any]:
    """Durably record reviewer-selected company identities before graph resume."""
    _require_candidate_review_role(user_role)
    with get_cursor() as (_, cur):
        run = get_run_for_update(run_id, cur)
        _authorize_locked_sourcing_run(run, user_id, user_role)
        _require_version(run, request.expected_version)
        _require_transition(run, AgentRunStatus.IDENTITY_REVIEW)
        persisted_candidates = list_candidates_with_cursor(run_id, cur)
        by_review_id: dict[str, dict[str, Any]] = {}
        for candidate in persisted_candidates:
            review_id = _legacy_candidate_review_id(candidate)
            if review_id in by_review_id:
                raise DomainError(
                    "SOURCING_IDENTITY_REVIEW_AMBIGUOUS",
                    "候选审核标识不唯一，请使用候选处置入口重新审核",
                    409,
                )
            by_review_id[review_id] = candidate
        for review_id, company_id in request.resolutions.items():
            candidate = by_review_id.get(review_id)
            if candidate is None or company_id not in _candidate_identity_options(candidate):
                raise DomainError(
                    "SOURCING_IDENTITY_OPTION_INVALID",
                    "所选企业不在当前候选主体选项中",
                    422,
                )
        updated = update_run_status(run_id, request.expected_version, AgentRunStatus.IDENTITY_REVIEW.value, cur=cur)
        if updated is None:
            _raise_version_conflict()
        append_event(
            run_id,
            updated["version"],
            "identity_resolution",
            {"identity_resolutions": dict(request.resolutions), "status": updated["status"]},
            cur=cur,
        )
    return updated


def get_candidate_decisions(run_id: str, user_id: str, user_role: str) -> dict[str, Any]:
    """Return the latest separately persisted disposition state for one Run."""
    with get_cursor() as (_, cur):
        run = get_run_for_update(run_id, cur)
        _authorize_locked_sourcing_run(run, user_id, user_role)
        candidates = list_candidates_with_cursor(run_id, cur)
        dispositions = list_sourcing_candidate_dispositions_with_cursor(run_id, cur)
        batches = list_candidate_review_events_with_cursor(run_id, cur)
    disposition_by_candidate = {str(row["candidate_id"]): row for row in dispositions}
    effective = []
    for candidate in candidates:
        row = disposition_by_candidate.get(str(candidate.get("candidate_id") or candidate.get("id")))
        if row is None:
            continue
        effective.append(_candidate_disposition_payload(candidate, row))
    latest_batch = batches[0] if batches else None
    resume_status = (
        str(latest_batch.get("resume_status"))
        if latest_batch
        else str(run.get("status") or "not_started").lower()
    )
    return {
        "run_id": str(run["id"]),
        "run_version": int(run["version"]),
        "dispositions": effective,
        "resume_status": resume_status,
        "new_run_id": str(latest_batch["new_run_id"]) if latest_batch and latest_batch.get("new_run_id") else None,
    }


def submit_candidate_decisions(
    run_id: str,
    payload: dict[str, Any],
    user_id: str,
    user_role: str,
    request_key: str,
) -> dict[str, Any]:
    """Persist a candidate review batch and its durable resume intent atomically."""
    _require_candidate_review_role(user_role)
    request_key = request_key.strip()
    if not request_key or len(request_key) > 255:
        raise DomainError("IDEMPOTENCY_KEY_INVALID", "Idempotency-Key 无效", 422)
    request = CandidateDecisionBatchRequest.model_validate(payload)
    serialized_request = request.model_dump(mode="json")
    request_hash = hashlib.sha256(
        json.dumps(serialized_request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    with get_cursor() as (_, cur):
        run = get_run_for_update(run_id, cur)
        _authorize_locked_sourcing_run(run, user_id, user_role)

        lock_candidate_review_request_with_cursor(user_id, request_key, cur)
        previous = get_candidate_review_event_by_request_with_cursor(user_id, request_key, cur)
        if previous is not None:
            if previous["request_hash"] != request_hash or str(previous["run_id"]) != run_id:
                raise DomainError(
                    "SOURCING_CANDIDATE_IDEMPOTENCY_CONFLICT",
                    "Idempotency-Key 已用于不同的候选处置请求",
                    409,
                )
            response = previous.get("response") or {}
            if isinstance(response, str):
                response = json.loads(response)
            return dict(response)

        _require_version(run, request.expected_version)
        terminal_reclassification = str(run.get("status")) in TERMINAL_STATUSES
        if terminal_reclassification and run.get("status") not in {
            AgentRunStatus.PARTIAL.value,
            AgentRunStatus.NEEDS_REVIEW.value,
        }:
            raise DomainError(
                "SOURCING_TERMINAL_DISPOSITION_INVALID",
                "当前终态不支持修改候选处置",
                409,
            )
        if not terminal_reclassification:
            _require_transition(run, AgentRunStatus.IDENTITY_REVIEW)
        persisted_candidates = list_candidates_with_cursor(run_id, cur)
        disposition_rows = list_sourcing_candidate_dispositions_with_cursor(run_id, cur)
        disposition_by_candidate = {str(row["candidate_id"]): row for row in disposition_rows}
        review_candidates: list[dict[str, Any]] = []
        for persisted in persisted_candidates:
            stable_id = str(persisted.get("candidate_id") or persisted.get("id") or "")
            if not stable_id:
                raise DomainError(
                    "SOURCING_CANDIDATE_KEY_UNAVAILABLE",
                    "候选缺少已持久化的稳定标识，无法提交审核",
                    409,
                )
            candidate = {**persisted, "candidate_key": stable_id}
            existing = disposition_by_candidate.get(stable_id)
            if existing:
                candidate.update(
                    disposition=str(existing["action"]),
                    reason=existing.get("reason"),
                )
                if existing.get("action") == "confirm" and existing.get("company_id"):
                    candidate.update(
                        company_id=str(existing["company_id"]),
                        identity_status="exact",
                        score_eligible=True,
                    )
            review_candidates.append(candidate)

        normalized_decisions = [item.model_dump(exclude_none=True) for item in request.decisions]
        if terminal_reclassification:
            existing_actions = {
                str(item["candidate_id"]): str(item["action"])
                for item in disposition_rows
            }
            if not normalized_decisions or any(
                item.get("action") != "defer"
                or existing_actions.get(str(item.get("candidate_key"))) != "exclude"
                for item in normalized_decisions
            ):
                raise DomainError(
                    "SOURCING_TERMINAL_DISPOSITION_INVALID",
                    "终态任务仅允许将已排除候选显式改为暂存",
                    409,
                )
        try:
            groups = apply_candidate_decisions(review_candidates, normalized_decisions)
        except ValueError as exc:
            raise DomainError("SOURCING_CANDIDATE_DECISION_INVALID", str(exc), 422) from exc

        batch_id = str(uuid4())
        submitted_by_key = {item["candidate_key"]: item for item in normalized_decisions}
        disposition_payloads: list[dict[str, Any]] = []
        for _group_name, candidates in groups.items():
            for candidate in candidates:
                candidate_key = str(candidate["candidate_key"])
                if terminal_reclassification and candidate_key not in submitted_by_key:
                    continue
                action = candidate.get("disposition")
                if action not in {"confirm", "defer", "exclude"}:
                    continue
                submitted = submitted_by_key.get(candidate_key, {})
                company_id = str(candidate["company_id"]) if action == "confirm" else None
                reason = submitted.get("reason") or candidate.get("reason")
                if not submitted and action == "defer" and not reason:
                    reason = "not_selected_this_round"
                upsert_sourcing_candidate_disposition_with_cursor(
                    run_id=run_id,
                    candidate_id=str(candidate["candidate_key"]),
                    action=action,
                    company_id=company_id,
                    reason=reason,
                    decided_by=user_id,
                    batch_id=batch_id,
                    cur=cur,
                )
                disposition_payloads.append({
                    "candidate_key": candidate_key,
                    "action": action,
                    "company_id": company_id,
                    "reason": reason,
                })

        has_eligible = bool(groups["eligible"])
        run_status = (
            str(run["status"])
            if terminal_reclassification
            else AgentRunStatus.IDENTITY_RESOLVING.value
            if has_eligible
            else AgentRunStatus.NEEDS_REVIEW.value
        )
        resume_status = (
            "followup_available"
            if terminal_reclassification
            else "pending"
            if has_eligible
            else "needs_review"
        )
        updated = (
            touch_terminal_run_with_cursor(
                cur, run_id, request.expected_version, run_status
            )
            if terminal_reclassification
            else update_run_status(run_id, request.expected_version, run_status, cur=cur)
        )
        if updated is None:
            _raise_version_conflict()
        append_event(
            run_id,
            int(updated["version"]),
            "candidate_dispositions_updated" if terminal_reclassification else "candidate_review_submitted",
            {
                "batch_id": batch_id,
                "dispositions": disposition_payloads,
                "resume_status": resume_status,
                "status": updated["status"],
            },
            cur=cur,
        )
        response = {
            "run_id": run_id,
            "run_version": int(updated["version"]),
            "dispositions": disposition_payloads,
            "resume_status": resume_status,
            "new_run_id": None,
        }
        insert_candidate_review_event_with_cursor(
            batch_id=batch_id,
            run_id=run_id,
            actor_id=user_id,
            request_key=request_key,
            request_hash=request_hash,
            expected_version=request.expected_version,
            run_version=int(updated["version"]),
            decisions=disposition_payloads,
            response=response,
            resume_status=resume_status,
            dispatch_status="pending" if has_eligible and not terminal_reclassification else "not_required",
            cur=cur,
        )
    return response


def persist_sourcing_policy_snapshot(run_id: str, policy: dict[str, Any]) -> dict[str, Any]:
    """Persist one immutable scoring policy and bind its UUID to the source Run."""
    with get_cursor() as (_, cur):
        run = get_run_for_update(run_id, cur)
        if run is None or run.get("run_type") != "sourcing_risk_v2":
            raise DomainError("AGENT_RUN_NOT_FOUND", "任务不存在", 404)
        snapshot = get_policy_snapshot_by_run_with_cursor(run_id, cur)
        if snapshot is None:
            snapshot = upsert_policy_snapshot_with_cursor(run_id, policy, cur)
        else:
            update_run_policy_snapshot_with_cursor(run_id, str(snapshot["id"]), cur)
    return snapshot


def get_sourcing_policy_snapshot(snapshot_id: str) -> dict[str, Any] | None:
    with get_cursor() as (_, cur):
        return get_policy_snapshot_by_id_with_cursor(snapshot_id, cur)


def get_sourcing_candidate_followup_context(child_run_id: str) -> dict[str, Any] | None:
    """Load the selected parent candidates and frozen policy for a child Run."""
    followup = get_candidate_followup_context(child_run_id)
    if followup is None:
        return None
    policy_snapshot_id = str(followup.get("policy_snapshot_id") or "")
    policy_snapshot = get_sourcing_policy_snapshot(policy_snapshot_id) if policy_snapshot_id else None
    if policy_snapshot is None:
        raise DomainError(
            "SOURCING_POLICY_SNAPSHOT_UNAVAILABLE",
            "父任务规则快照不可用，不能恢复候选任务",
            409,
        )
    with get_cursor() as (_, cur):
        candidates = list_candidates_with_cursor(child_run_id, cur)
    candidate_ids = {str(item) for item in followup.get("child_candidate_ids") or []}
    selected = [
        candidate for candidate in candidates
        if str(candidate.get("candidate_id") or candidate.get("id")) in candidate_ids
    ]
    if len(selected) != len(candidate_ids):
        raise DomainError(
            "SOURCING_FOLLOWUP_CANDIDATES_UNAVAILABLE",
            "恢复任务候选快照不完整，不能继续执行",
            409,
        )
    return {
        **followup,
        "candidates": selected,
        "policy_snapshot_id": policy_snapshot_id,
        "policy_snapshot": dict(policy_snapshot.get("policy") or {}),
    }


def get_candidate_review_batch_for_execution(batch_id: str) -> dict[str, Any] | None:
    """Read the currently leased candidate batch for the persistent worker."""
    batch = repo_get_candidate_review_batch_for_execution(batch_id)
    if batch is None:
        return None
    return {
        **batch,
        "fencing_token": int(batch.get("fencing_token") or 0),
        "run_version": int(batch.get("run_version") or 0),
    }


def mark_candidate_review_batch_consumed(batch_id: str, fencing_token: int) -> bool:
    with get_cursor() as (_, cur):
        return mark_candidate_review_batch_consumed_with_cursor(batch_id, fencing_token, cur)


def get_candidate_review_groups_for_batch(batch_id: str) -> dict[str, Any]:
    """Rebuild the resume groups from PostgreSQL candidates and dispositions."""
    batch = repo_get_candidate_review_batch_for_execution(batch_id)
    if batch is None or batch.get("dispatch_status") != "processing":
        raise DomainError("SOURCING_CANDIDATE_BATCH_UNAVAILABLE", "候选恢复批次不可用", 409)
    run_id = str(batch["run_id"])
    with get_cursor() as (_, cur):
        candidates = list_candidates_with_cursor(run_id, cur)
        disposition_rows = list_sourcing_candidate_dispositions_with_cursor(run_id, cur)
    by_candidate = {str(row["candidate_id"]): row for row in disposition_rows}
    review_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "")
        if not candidate_id:
            raise DomainError("SOURCING_CANDIDATE_KEY_UNAVAILABLE", "候选缺少已持久化标识", 409)
        review_candidate = {**candidate, "candidate_key": candidate_id}
        disposition = by_candidate.get(candidate_id)
        if disposition:
            review_candidate.update(
                disposition=str(disposition["action"]),
                reason=disposition.get("reason"),
            )
            if disposition.get("action") == "confirm" and disposition.get("company_id"):
                review_candidate.update(
                    company_id=str(disposition["company_id"]),
                    identity_status="exact",
                    score_eligible=True,
                )
        review_candidates.append(review_candidate)
    decisions = list(batch.get("decisions") or [])
    try:
        groups = apply_candidate_decisions(review_candidates, decisions)
    except ValueError as exc:
        raise DomainError("SOURCING_CANDIDATE_DECISION_INVALID", str(exc), 409) from exc
    return {**groups, "all_candidates": [*groups["eligible"], *groups["deferred"], *groups["excluded"]]}


def derive_candidate_followup(
    run_id: str,
    candidate_keys: list[str],
    user_id: str,
    user_role: str,
    request_key: str,
) -> dict[str, Any]:
    """Create a child Run for explicitly resumed deferred candidates."""
    _require_candidate_review_role(user_role)
    request_key = request_key.strip()
    if not request_key or len(request_key) > 255:
        raise DomainError("IDEMPOTENCY_KEY_INVALID", "Idempotency-Key 无效", 422)
    if not candidate_keys or any(not isinstance(key, str) or not key.strip() for key in candidate_keys):
        raise DomainError("SOURCING_FOLLOWUP_CANDIDATES_INVALID", "必须选择至少一个有效候选", 422)
    if len(set(candidate_keys)) != len(candidate_keys):
        raise DomainError("SOURCING_FOLLOWUP_CANDIDATES_INVALID", "candidate_key 不得重复", 422)
    request_hash = hashlib.sha256(
        json.dumps({"run_id": run_id, "candidate_keys": candidate_keys}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    with get_cursor() as (_, cur):
        parent = get_run_for_update(run_id, cur)
        _authorize_locked_sourcing_run(parent, user_id, user_role)
        lock_candidate_followup_request_with_cursor(user_id, request_key, cur)
        previous = get_candidate_followup_event_by_request_with_cursor(user_id, request_key, cur)
        if previous is not None:
            if previous["request_hash"] != request_hash or str(previous["parent_run_id"]) != run_id:
                raise DomainError("SOURCING_FOLLOWUP_IDEMPOTENCY_CONFLICT", "Idempotency-Key 已用于不同的恢复请求", 409)
            response = previous.get("response") or {}
            if isinstance(response, str):
                response = json.loads(response)
            return {**dict(response), "_start_required": False}

        if parent["status"] not in {
            AgentRunStatus.PARTIAL.value,
            AgentRunStatus.NEEDS_REVIEW.value,
        }:
            raise DomainError("SOURCING_FOLLOWUP_PARENT_NOT_TERMINAL", "父任务尚未终态，不能派生恢复任务", 409)
        policy_snapshot_id = str(parent.get("policy_snapshot_id") or "") or None
        policy_snapshot = (
            get_policy_snapshot_by_id_with_cursor(policy_snapshot_id, cur)
            if policy_snapshot_id else None
        )
        if policy_snapshot is None:
            raise DomainError("SOURCING_POLICY_SNAPSHOT_UNAVAILABLE", "父任务规则快照不可用，不能派生恢复任务", 409)

        candidates = list_candidates_with_cursor(run_id, cur)
        candidates_by_id = {
            str(candidate.get("candidate_id") or candidate.get("id")): candidate
            for candidate in candidates
        }
        dispositions = list_sourcing_candidate_dispositions_with_cursor(run_id, cur)
        disposition_by_id = {str(item["candidate_id"]): item for item in dispositions}
        selected_candidates: list[dict[str, Any]] = []
        for candidate_key in candidate_keys:
            candidate = candidates_by_id.get(candidate_key)
            disposition = disposition_by_id.get(candidate_key)
            if candidate is None or disposition is None:
                raise DomainError("SOURCING_FOLLOWUP_CANDIDATE_NOT_FOUND", "候选不存在或没有可恢复处置", 404)
            if disposition.get("action") == "exclude":
                raise DomainError("SOURCING_FOLLOWUP_CANDIDATE_EXCLUDED", "排除候选需先显式改为暂存后才能恢复", 409)
            if disposition.get("action") != "defer":
                raise DomainError("SOURCING_FOLLOWUP_CANDIDATE_NOT_DEFERRED", "只有本轮暂存候选可以派生恢复任务", 409)
            snapshot = {
                key: value
                for key, value in candidate.items()
                if key not in {"id", "candidate_id", "source", "status", "updated_at"}
            }
            snapshot.update(
                candidate_key=f"followup:{run_id}:{candidate_key}",
                parent_candidate_id=candidate_key,
            )
            selected_candidates.append(snapshot)

        child = insert_run(
            run_type="sourcing_risk_v2",
            requirement=dict(parent.get("requirement") or {}),
            user_id=user_id,
            status=AgentRunStatus.CREATED.value,
            cur=cur,
        )
        update_run_policy_snapshot_with_cursor(str(child["id"]), policy_snapshot_id, cur)
        child_snapshot = persist_run_snapshot(
            str(child["id"]), candidates=selected_candidates, cur=cur
        )
        child_candidates = list(child_snapshot.get("candidates") or [])
        child_candidate_ids = [str(item["id"]) for item in child_candidates]
        insert_candidate_followup_with_cursor(
            parent_run_id=run_id,
            child_run_id=str(child["id"]),
            created_by=user_id,
            source_candidate_ids=list(candidate_keys),
            child_candidate_ids=child_candidate_ids,
            policy_snapshot_id=policy_snapshot_id,
            cur=cur,
        )
        response = {
            "parent_run_id": run_id,
            "run_id": str(child["id"]),
            "resume_url": f"/sourcing?run_id={child['id']}",
        }
        append_event(
            str(child["id"]),
            int(child.get("version") or 1),
            "candidate_followup_created",
            {"parent_run_id": run_id, "candidate_count": len(child_candidate_ids), "status": child["status"]},
            cur=cur,
        )
        insert_candidate_followup_event_with_cursor(
            event_id=str(uuid4()),
            parent_run_id=run_id,
            child_run_id=str(child["id"]),
            actor_id=user_id,
            request_key=request_key,
            request_hash=request_hash,
            response=response,
            cur=cur,
        )
    return {**response, "_start_required": True}


def cancel_run(
    run_id: str, expected_version: int, user_id: str, user_role: str
) -> dict[str, Any]:
    run = _get_authorized_run(run_id, user_id, user_role)
    _require_version(run, expected_version)
    if run.get("run_type") == "agent_harness" or (run.get("requirement") or {}).get("_managed_chat"):
        from app.domains.agent_run.coordinator import run_coordinator

        # Authorization above also covers administrators acting on owned Runs.
        return run_coordinator.cancel(run_id, str(run.get("user_id") or ""), expected_version=expected_version)
    _require_transition(run, AgentRunStatus.CANCELLED)
    with get_cursor() as (_, cur):
        updated = update_run_status(run_id, expected_version, AgentRunStatus.CANCELLED.value, cur=cur)
        if updated is None:
            _raise_version_conflict()
        if run.get("run_type") == "sourcing_risk_v2":
            cancel_pending_candidate_review_batches_with_cursor(run_id, cur)
        append_event(run_id, updated["version"], "done", {"status": updated["status"]}, cur=cur)
    return updated


def cancel_active_harness_run(
    session_id: str,
    user_id: str,
    user_role: str,
) -> dict[str, Any] | None:
    """Cancel through the same transaction owner used by graph completion."""
    from app.domains.agent_run.coordinator import run_coordinator

    del user_role
    run = get_latest_harness_run_for_session(session_id, user_id)
    if run is None:
        return None
    return run_coordinator.cancel(str(run["id"]), user_id)


def decide_action_proposal(
    run_id: str,
    approval_id: str,
    request: ApprovalDecisionRequest,
    user_id: str,
    user_role: str,
) -> dict[str, Any]:
    from app.domains.sourcing_risk.action_service import decide_action_proposal as decide_v2_action_proposal

    return decide_v2_action_proposal(run_id, approval_id, request, user_id, user_role)


def stream_events(
    run_id: str, last_event_id: int, user_id: str, user_role: str
) -> Iterator[dict[str, Any]]:
    cursor = last_event_id
    while True:
        run = get_sourcing_risk_run(run_id, user_id, user_role)
        events = list_events_after(run_id, cursor)
        if events:
            for event in events:
                cursor = event["event_id"]
                yield _event_for_stream(event, run)
            if run["status"] in TERMINAL_STATUSES:
                return
            continue
        if run["status"] in TERMINAL_STATUSES:
            return
        time.sleep(15)
        yield {"event_type": "keepalive", "data": {}}


def get_harness_run_status(
    run_id: str, user_id: str, user_role: str
) -> dict[str, Any]:
    """Return the durable Run/execution/job state without graph internals."""
    run = _get_authorized_run(run_id, user_id, user_role)
    with get_cursor() as (_, cur):
        cur.execute(
            """SELECT state,attempt_id,fencing_token,owner_id,lease_until,quality,
                      pause_reason,recovery_count
               FROM agent_run_executions WHERE run_id=%s""",
            (run_id,),
        )
        execution_row = cur.fetchone()
        cur.execute(
            """SELECT status,attempts,recoveries,max_recoveries,available_at,
                      lease_owner,lease_until,last_error,completed_at
               FROM agent_execution_jobs WHERE run_id=%s""",
            (run_id,),
        )
        job_row = cur.fetchone()
    execution = None
    if execution_row:
        execution = {
            "state": execution_row[0],
            "attempt_id": str(execution_row[1]) if execution_row[1] else None,
            "fencing_token": execution_row[2],
            "owner_id": execution_row[3],
            "lease_until": execution_row[4],
            "quality": execution_row[5],
            "pause_reason": execution_row[6],
            "recovery_count": execution_row[7],
        }
    job = None
    if job_row:
        job = {
            "status": job_row[0],
            "attempts": job_row[1],
            "recoveries": job_row[2],
            "max_recoveries": job_row[3],
            "available_at": job_row[4],
            "lease_owner": job_row[5],
            "lease_until": job_row[6],
            "last_error": job_row[7],
            "completed_at": job_row[8],
        }
    requirement = dict(run.get("requirement") or {})
    return {
        "run_id": str(run["id"]),
        "session_id": str(run.get("session_id") or ""),
        "turn_id": str(requirement.get("turn_id") or ""),
        "status": run["status"],
        "error_code": run.get("error_code"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "completed_at": run.get("completed_at"),
        "execution": execution,
        "job": job,
    }


def stream_harness_events(
    run_id: str, last_event_id: int, user_id: str, user_role: str
) -> Iterator[dict[str, Any]]:
    """Replay the chat Harness event log without projecting V2 business tables."""
    from app.domains.agent_run.coordinator import run_coordinator

    cursor = last_event_id
    while True:
        run = _get_authorized_run(run_id, user_id, user_role)
        events = list_events_after(run_id, cursor)
        if events:
            for event in events:
                cursor = int(event["event_id"])
                yield {
                    "event_id": cursor,
                    "event_type": event["event_type"],
                    "data": dict(event.get("payload") or {}),
                }
            if run["status"] in TERMINAL_STATUSES or run_coordinator.is_paused(run_id):
                return
            continue
        if run["status"] in TERMINAL_STATUSES or run_coordinator.is_paused(run_id):
            return
        time.sleep(2)
        yield {"event_type": "keepalive", "data": {}}


def append_orchestration_event(run_id: str, event_type: str, payload: dict[str, Any]) -> int | None:
    """Append a typed graph event through the service boundary without graph SQL access."""
    run = get_run(run_id)
    if run is None:
        return None
    event = append_event(run_id, run["version"], event_type, payload)
    return int(event["event_id"])


def persist_supervisor_snapshot(
    run_id: str,
    task_status: str,
    event_type: str,
    snapshot: dict[str, Any],
) -> int | None:
    """Persist a checkpoint-adjacent Supervisor snapshot as a replayable event.

    Supervisor planning and analysis results do not map to supplier-master
    tables. Keeping them in the existing agent-run event stream preserves the
    durable/SSE boundary without introducing a second store or a business write.
    """
    durable_status = _SUPERVISOR_DURABLE_STATUSES.get(task_status)
    from app.domains.agent_run.coordinator import current_execution, require_active

    permit = current_execution.get()
    with get_cursor() as (_, cur):
        run = get_orchestration_run_for_update(run_id, cur)
        if run is None:
            return None
        require_active(cur, run, permit)
        if permit and durable_status in {"COMPLETED", "PARTIAL", "FAILED", "NEEDS_REVIEW"}:
            durable_status = None
        if permit and event_type == "done":
            event_type = "supervisor_finalized"
        updated = run
        if durable_status and run["status"] != durable_status:
            updated = update_run_status(
                run_id, run["version"], durable_status, cur=cur
            )
            if updated is None:
                _raise_version_conflict()
        execution_snapshot = _execution_snapshot_from_event(snapshot)
        event_payload = {
            "task_status": task_status,
            **dict(snapshot),
            "status": updated["status"],
        }
        if _has_execution_snapshot_data(execution_snapshot):
            event_payload["execution_snapshot"] = execution_snapshot.model_dump(mode="json")
        event = append_event(
            run_id,
            updated["version"],
            event_type,
            event_payload,
            cur=cur,
        )
        for trace_event in build_agent_trace_events(
            event_type, task_status, snapshot
        ):
            append_event(
                run_id,
                updated["version"],
                TRACE_EVENT_TYPE,
                trace_event,
                cur=cur,
            )
    return int(event["event_id"])


def load_execution_snapshot(
    run_id: str,
    run: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Rebuild a safe graph resume input from durable execution snapshots.

    Completed subtasks remain in the result ledger but are deliberately excluded
    from ``resumable_subtasks``. Only pending approval actions are returned, so a
    resume cannot expose an approved/rejected action branch as if it were paused.
    """
    run = run or get_orchestration_run(run_id)
    if run is None:
        return None

    snapshot = AgentExecutionSnapshot()
    for event in list_execution_snapshot_events(run_id):
        payload = event.get("payload") if isinstance(event, dict) else None
        raw_snapshot = payload.get("execution_snapshot") if isinstance(payload, dict) else None
        if not isinstance(raw_snapshot, dict):
            continue
        snapshot = _merge_execution_snapshot(snapshot, raw_snapshot)

    completed_subtask_ids = [
        task_id
        for task_id, result in snapshot.subtask_results.items()
        if result.get("status") == "completed"
    ]
    completed_ids = set(completed_subtask_ids)
    resumable_subtasks = [
        task for task in snapshot.task_matrix
        if str(task.get("subtask_id") or task.get("task_id") or "") not in completed_ids
    ]
    pending_approvals = (
        [
            approval for approval in snapshot.pending_approvals
            if approval.get("status") == "pending"
        ]
        if run.get("status") == AgentRunStatus.ACTION_PENDING.value
        else []
    )
    return {
        **snapshot.model_dump(mode="json"),
        "completed_subtask_ids": completed_subtask_ids,
        "resumable_subtasks": resumable_subtasks,
        "pending_approvals": pending_approvals,
    }


def _execution_snapshot_from_event(snapshot: dict[str, Any]) -> AgentExecutionSnapshot:
    """Normalize one Supervisor event into a mergeable execution checkpoint."""
    task_matrix = snapshot.get("task_matrix")
    if not isinstance(task_matrix, list):
        plan = snapshot.get("plan")
        task_matrix = plan.get("tasks", []) if isinstance(plan, dict) else []

    subtask_results: dict[str, dict[str, Any]] = {}
    task_id = snapshot.get("task_id")
    result = snapshot.get("result")
    if isinstance(task_id, str) and isinstance(result, dict):
        subtask_results[task_id] = dict(result)
    raw_results = snapshot.get("agent_results")
    if isinstance(raw_results, dict):
        subtask_results.update({
            str(result_id): dict(item)
            for result_id, item in raw_results.items()
            if isinstance(item, dict)
        })

    loops = {
        key.removesuffix("_loop"): dict(value)
        for key, value in snapshot.items()
        if key.endswith("_loop") and isinstance(value, dict)
    }
    validators = {
        key.removesuffix("_validation"): dict(value)
        for key, value in snapshot.items()
        if key.endswith("_validation") and isinstance(value, dict)
    }
    pending_approvals = snapshot.get("pending_approvals")
    return AgentExecutionSnapshot(
        task_matrix=[item for item in task_matrix if isinstance(item, dict)],
        subtask_results=subtask_results,
        loops=loops,
        validators=validators,
        pending_approvals=[
            dict(item) for item in pending_approvals
            if isinstance(item, dict)
        ] if isinstance(pending_approvals, list) else [],
    )


def _has_execution_snapshot_data(snapshot: AgentExecutionSnapshot) -> bool:
    return any((
        snapshot.task_matrix,
        snapshot.subtask_results,
        snapshot.loops,
        snapshot.validators,
        snapshot.pending_approvals,
    ))


def _merge_execution_snapshot(
    current: AgentExecutionSnapshot,
    incoming: dict[str, Any],
) -> AgentExecutionSnapshot:
    """Overlay one event checkpoint without discarding prior successful results."""
    checkpoint = AgentExecutionSnapshot.model_validate(incoming)
    return AgentExecutionSnapshot(
        task_matrix=checkpoint.task_matrix or current.task_matrix,
        subtask_results={**current.subtask_results, **checkpoint.subtask_results},
        loops={**current.loops, **checkpoint.loops},
        validators={**current.validators, **checkpoint.validators},
        pending_approvals=(
            checkpoint.pending_approvals
            if checkpoint.pending_approvals else current.pending_approvals
        ),
    )


def create_supervisor_action_proposals(
    run_id: str, approvals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Persist Supervisor approvals as the existing action proposal records."""
    run = get_orchestration_run(run_id)
    if run is None:
        raise DomainError("AGENT_RUN_NOT_FOUND", "任务不存在", 404)
    user_id = str(run.get("user_id") or "")
    if not user_id:
        raise DomainError("AGENT_ACTION_PROPOSAL_FORBIDDEN", "任务缺少发起人", 403)

    from app.domains.sourcing_risk.action_service import create_action_proposal

    persisted: list[dict[str, Any]] = []
    for approval in approvals:
        original_id = str(approval["approval_id"])
        action_type = _SUPERVISOR_ACTION_TYPES.get(
            str(approval["action_type"]), str(approval["action_type"])
        )
        expires_at = str(
            approval.get("expires_at")
            or (datetime.now(timezone.utc) + SUPERVISOR_APPROVAL_TTL).isoformat()
        )
        target = dict(approval.get("target") or {})
        action_payload = dict(target)
        action_payload.pop("expires_at", None)
        if action_type in {"add_watchlist", "remove_watchlist"}:
            from app.graphs.harness.actions import build_action_hash, normalize_action_arguments

            tool_name = {
                "add_watchlist": "add_to_watchlist",
                "remove_watchlist": "remove_from_watchlist",
            }[action_type]
            action_payload = normalize_action_arguments(tool_name, action_payload)
            action_payload["_harness_action"] = {
                "tool_name": tool_name,
                "action_hash": build_action_hash(tool_name, dict(action_payload)),
                "session_id": str(run.get("session_id") or ""),
                "expires_at": expires_at,
            }
        proposal = create_action_proposal(
            run_id,
            action_type,
            action_payload,
            f"supervisor:{run_id}:{original_id}",
            # An external identity candidate ID is an evidence key, not an
            # ``agent_run_candidates`` row.  Passing it to the durable action
            # binder would incorrectly reject an otherwise valid confirmation
            # with “候选企业不属于任务”.
            candidate_id=(
                target.get("candidate_id")
                if not isinstance(target.get("external_identity"), dict)
                else None
            ),
            user_id=user_id,
            user_role=_supervisor_actor_role(run),
            expected_version=int(run["version"]),
        )
        persisted.append({**approval, "approval_id": str(proposal["id"]), "expires_at": expires_at})
    return persisted


def execute_supervisor_approved_action(run_id: str, approval_id: str) -> dict[str, Any] | None:
    """Enter the existing approved-only, idempotent V2 action boundary.

    The action service reloads the durable proposal and refuses execution unless
    its persisted status is approved. Supervisor code never calls repositories
    or mutation adapters directly.
    """
    if not run_id or not approval_id:
        raise ValueError("run_id 和 approval_id 不能为空")
    run = get_orchestration_run(run_id)
    if run is None or not run.get("user_id"):
        raise DomainError("AGENT_ACTION_EXECUTION_CONTEXT_INVALID", "任务缺少审批人绑定", 409)

    from app.domains.sourcing_risk.action_service import execute_sourcing_risk_action

    return execute_sourcing_risk_action(
        {
            "payload": {
                "run_id": run_id,
                "proposal_id": approval_id,
                "approver_id": str(run["user_id"]),
            }
        }
    )


def approve_supervisor_action_proposal(run_id: str, approval_id: str) -> None:
    """Record the explicit Supervisor approval before invoking action delivery."""
    run = get_orchestration_run(run_id)
    if run is None:
        raise DomainError("AGENT_RUN_NOT_FOUND", "任务不存在", 404)

    from app.domains.sourcing_risk.action_service import decide_action_proposal

    decide_action_proposal(
        run_id,
        approval_id,
        ApprovalDecisionRequest(expected_version=int(run["version"]), decision="approved"),
        str(run["user_id"]),
        _supervisor_actor_role(run),
    )


def approve_supervisor_action_proposals(run_id: str, approval_ids: list[str]) -> None:
    """Record an aggregate Supervisor approval through one run-state transition."""
    run = get_orchestration_run(run_id)
    if run is None:
        raise DomainError("AGENT_RUN_NOT_FOUND", "任务不存在", 404)

    from app.domains.sourcing_risk.action_service import decide_action_proposals

    decide_action_proposals(
        run_id,
        approval_ids,
        ApprovalDecisionRequest(expected_version=int(run["version"]), decision="approved"),
        str(run["user_id"]),
        _supervisor_actor_role(run),
    )


def reject_supervisor_action_proposals(run_id: str, approval_ids: list[str]) -> None:
    from app.domains.sourcing_risk.action_service import decide_action_proposals

    run = get_orchestration_run(run_id)
    if run is None:
        raise ValueError("Agent Run 不存在")
    decide_action_proposals(run_id, approval_ids,
        ApprovalDecisionRequest(expected_version=int(run["version"]), decision="rejected"),
        str(run["user_id"]), _supervisor_actor_role(run))


def _supervisor_actor_role(run: dict[str, Any]) -> str:
    from app.domains.agent_run.coordinator import current_execution

    if current_execution.get() is None:
        return "purchaser"  # Historical directed graph compatibility only.
    with get_cursor() as (_, cur):
        cur.execute("SELECT role,is_active FROM users WHERE id=%s", (run["user_id"],))
        actor = cur.fetchone()
        if not actor or not actor[1] or actor[0] not in {"admin", "purchaser", "analyst"}:
            raise PermissionError("当前用户没有操作权限")
        return str(actor[0])


def record_orchestration_state(
    run_id: str, status: str, event_type: str, payload: dict[str, Any]
) -> int | None:
    """Atomically transition a graph run and append the SSE-visible typed event."""
    run = get_run(run_id)
    if run is None:
        return None
    try:
        target = AgentRunStatus(status)
    except ValueError as exc:
        raise DomainError("AGENT_RUN_INVALID_STATE", "任务状态无效", 409) from exc
    _require_transition(run, target)
    with get_cursor() as (_, cur):
        updated = update_run_status(run_id, run["version"], target.value, cur=cur)
        if updated is None:
            _raise_version_conflict()
        event = append_event(
            run_id,
            updated["version"],
            event_type,
            {**payload, "status": updated["status"]},
            cur=cur,
        )
    return int(event["event_id"])


def get_orchestration_run_for_update(run_id: str, cur: Any) -> dict[str, Any] | None:
    """Expose the locked graph Run only to the orchestration persistence command."""
    return get_run_for_update(run_id, cur)


def persist_orchestration_snapshot(
    run_id: str,
    status: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    candidates: list[dict[str, Any]] | None = None,
    evidence_by_company_id: dict[str, list[dict[str, Any]]] | None = None,
    evidence_reviews: dict[str, dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    raw_payloads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Atomically persist graph-owned collections, status, and replay event.

    This writes only agent-run collections. It deliberately does not create or modify
    company/supplier master records, so all business-master mutations remain approval-gated.
    """
    try:
        target = AgentRunStatus(status)
    except ValueError as exc:
        raise DomainError("AGENT_RUN_INVALID_STATE", "任务状态无效", 409) from exc
    staging_owner = _staging_owner(run_id, raw_payloads or [])
    try:
        staged_payloads = stage_raw_payloads(raw_payloads or [], staging_owner=staging_owner)
    except RawPayloadStagingError as exc:
        _record_and_compensate(exc.compensation_payloads, "mongo_staging_failed")
        raise
    try:
        with get_cursor() as (_, cur):
            run = get_orchestration_run_for_update(run_id, cur)
            if run is None:
                raise DomainError("AGENT_RUN_NOT_FOUND", "任务不存在", 404)
            _require_transition(run, target)
            snapshot = persist_run_snapshot(
                run_id,
                candidates=candidates,
                evidence_by_company_id=evidence_by_company_id,
                evidence_reviews=evidence_reviews,
                decisions=decisions,
                cur=cur,
            )
            updated = update_run_status(run_id, run["version"], target.value, cur=cur)
            if updated is None:
                _raise_version_conflict()
            append_event(
                run_id,
                updated["version"],
                event_type,
                {**payload, "status": updated["status"]},
                cur=cur,
            )
    except Exception:
        _record_and_compensate(staged_payloads, "postgres_snapshot_failed")
        raise
    try:
        commit_raw_payloads(staged_payloads)
    except Exception:
        _record_and_compensate(staged_payloads, "mongo_commit_failed")
        raise
    return snapshot


def _record_compensations(payloads: list[dict[str, Any]], reason: str) -> None:
    try:
        upsert_raw_payload_compensations(
            [
                {
                    "run_id": payload["run_id"],
                    "raw_payload_ref": payload["raw_payload_ref"],
                    "company_id": payload.get("company_id"),
                    "last_error": reason,
                    "staging_owner": payload["staging_owner"],
                }
                for payload in payloads
            ]
        )
    except Exception as exc:
        raise DomainError(
            "AGENT_RUN_RECOVERY_UNAVAILABLE", "原始证据补偿记录无法持久化，请立即重试", 503
        ) from exc


def _record_and_compensate(payloads: list[dict[str, Any]], reason: str) -> None:
    """Persist recovery metadata and always attempt owned Mongo cleanup."""
    record_error: Exception | None = None
    try:
        _record_compensations(payloads, reason)
    except Exception as exc:
        record_error = exc
    try:
        compensate_raw_payloads(payloads, reason=reason)
    except Exception as cleanup_error:
        if record_error is None:
            record_error = cleanup_error
    if record_error is not None:
        raise record_error


def _staging_owner(run_id: str, raw_payloads: list[dict[str, Any]]) -> str:
    del run_id, raw_payloads
    return str(uuid4())


def get_orchestration_run(run_id: str) -> dict[str, Any] | None:
    """Load a durable graph input through the run service boundary."""
    return get_run(run_id)


def _event_for_stream(event: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event["payload"])
    renderable_fields = {
        "version": event.get("version", run["version"]),
        "candidates": run.get("candidates", []),
        "evidence_by_company_id": run.get("evidence_by_company_id", {}),
        "evidence_reviews": run.get("evidence_reviews", {}),
        "decisions": run.get("decisions", []),
        "action_proposals": run.get("action_proposals", []),
        "approvals": run.get("approvals", []),
        "run": run,
    }
    return {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "data": {**payload, **renderable_fields},
    }


def _require_version(run: dict[str, Any], expected_version: int) -> None:
    if run["version"] != expected_version:
        _raise_version_conflict()


def _raise_version_conflict() -> None:
    raise DomainError("AGENT_RUN_VERSION_CONFLICT", "任务版本已变更", 409)


def _require_transition(run: dict[str, Any], target: AgentRunStatus) -> None:
    try:
        current = AgentRunStatus(run["status"])
    except ValueError as exc:
        raise DomainError("AGENT_RUN_INVALID_STATE", "任务状态无效", 409) from exc
    if target != current and target not in ALLOWED_STATUS_TRANSITIONS[current]:
        raise DomainError("AGENT_RUN_INVALID_STATE", "任务当前状态不允许此操作", 409)


def _require_candidate_review_role(user_role: str) -> None:
    if user_role not in {"admin", "purchaser", "analyst"}:
        raise DomainError("SOURCING_CANDIDATE_REVIEW_FORBIDDEN", "当前用户无权审核寻源候选", 403)


def _authorize_locked_sourcing_run(
    run: dict[str, Any] | None, user_id: str, user_role: str,
) -> None:
    if run is None or run.get("run_type") != "sourcing_risk_v2":
        raise DomainError("AGENT_RUN_NOT_FOUND", "任务不存在", 404)
    if user_role != "admin" and str(run.get("user_id") or "") != user_id:
        raise DomainError("AGENT_RUN_NOT_FOUND", "任务不存在", 404)


def _legacy_candidate_review_id(candidate: dict[str, Any]) -> str:
    return str(
        candidate.get("company_id")
        or candidate.get("supplier_id")
        or candidate.get("supplier_name")
        or candidate.get("identity_status")
        or ""
    )


def _candidate_identity_options(candidate: dict[str, Any]) -> set[str]:
    options = candidate.get("identity_candidates") or []
    if not isinstance(options, list):
        return set()
    return {
        str(option["company_id"])
        for option in options
        if isinstance(option, dict) and option.get("company_id")
    }


def _candidate_disposition_payload(
    candidate: dict[str, Any], disposition: dict[str, Any],
) -> dict[str, Any]:
    return {
        **candidate,
        "candidate_key": str(candidate.get("candidate_id") or candidate.get("id")),
        "candidate_company_id": candidate.get("company_id"),
        "action": disposition.get("action"),
        "company_id": str(disposition["company_id"]) if disposition.get("company_id") else None,
        "reason": disposition.get("reason"),
        "updated_at": disposition.get("updated_at"),
    }
