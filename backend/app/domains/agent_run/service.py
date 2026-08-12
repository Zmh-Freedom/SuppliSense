"""Framework-independent lifecycle commands for sourcing-risk agent runs."""

import time
from collections.abc import Iterator
from typing import Any

from app.core.errors import DomainError
from app.db.postgres import get_cursor
from app.domains.agent_run.models import ALLOWED_STATUS_TRANSITIONS, AgentRunStatus
from app.domains.agent_run.repo import (
    append_event,
    get_run,
    get_run_for_user,
    insert_approval_decision,
    insert_run,
    list_events_after,
    update_run_requirement,
    update_run_status,
)
from app.domains.agent_run.schemas import (
    ApprovalDecisionRequest,
    ClarificationRequest,
    CreateSourcingRiskRunRequest,
    IdentityResolutionRequest,
)

TERMINAL_STATUSES = frozenset(
    {
        AgentRunStatus.COMPLETED.value,
        AgentRunStatus.PARTIAL.value,
        AgentRunStatus.NEEDS_REVIEW.value,
        AgentRunStatus.ACTION_FAILED.value,
        AgentRunStatus.FAILED.value,
        AgentRunStatus.CANCELLED.value,
    }
)


def can_read_all_agent_runs(user_role: str) -> bool:
    """Only administrators may bypass creator scoping for operational recovery."""
    return user_role == "admin"


def create_sourcing_risk_run(
    request: CreateSourcingRiskRunRequest, user_id: str, user_role: str
) -> dict[str, Any]:
    del user_role
    with get_cursor() as (_, cur):
        run = insert_run(
            run_type="sourcing_risk_v2",
            requirement=request.model_dump(),
            user_id=user_id,
            cur=cur,
        )
        append_event(run["id"], run["version"], "stage", {"status": run["status"]}, cur=cur)
    return run


def get_sourcing_risk_run(run_id: str, user_id: str, user_role: str) -> dict[str, Any]:
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
    run = get_sourcing_risk_run(run_id, user_id, user_role)
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
    run = get_sourcing_risk_run(run_id, user_id, user_role)
    _require_version(run, request.expected_version)
    _require_transition(run, AgentRunStatus.IDENTITY_REVIEW)
    with get_cursor() as (_, cur):
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


def cancel_run(
    run_id: str, expected_version: int, user_id: str, user_role: str
) -> dict[str, Any]:
    run = get_sourcing_risk_run(run_id, user_id, user_role)
    _require_version(run, expected_version)
    _require_transition(run, AgentRunStatus.CANCELLED)
    with get_cursor() as (_, cur):
        updated = update_run_status(run_id, expected_version, AgentRunStatus.CANCELLED.value, cur=cur)
        if updated is None:
            _raise_version_conflict()
        append_event(run_id, updated["version"], "done", {"status": updated["status"]}, cur=cur)
    return updated


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
                yield _event_for_stream(event)
            if run["status"] in TERMINAL_STATUSES:
                return
            continue
        if run["status"] in TERMINAL_STATUSES:
            return
        time.sleep(15)
        yield {"event_type": "keepalive", "data": {}}


def append_orchestration_event(run_id: str, event_type: str, payload: dict[str, Any]) -> int | None:
    """Append a typed graph event through the service boundary without graph SQL access."""
    run = get_run(run_id)
    if run is None:
        return None
    event = append_event(run_id, run["version"], event_type, payload)
    return int(event["event_id"])


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


def get_orchestration_run(run_id: str) -> dict[str, Any] | None:
    """Load a durable graph input through the run service boundary."""
    return get_run(run_id)


def _event_for_stream(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "data": event["payload"],
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
