"""Adapter between Harness action contracts and the durable V2 action service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.graphs.harness.actions import ActionProposal, build_action_hash


_TO_DURABLE_ACTION = {
    "add_to_watchlist": "add_watchlist",
    "select_external_supplier_candidate": "add_watchlist",
}
_HARNESS_METADATA_KEY = "_harness_action"


def persist_action_proposal(
    proposal: ActionProposal,
    *,
    user_role: str,
    expected_version: int,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Persist a Harness proposal using the existing PostgreSQL transaction boundary."""
    action_type = _TO_DURABLE_ACTION.get(proposal.tool_name)
    if action_type is None:
        raise ValueError(f"写工具 {proposal.tool_name} 尚未接入 Durable Action Service")
    payload = dict(proposal.arguments)
    if action_type == "add_watchlist" and candidate_id is None and payload.get("target_source") != "conversation_state":
        raise ValueError("加入监控提案必须显式绑定 conversation_state")
    payload[_HARNESS_METADATA_KEY] = {
        "tool_name": proposal.tool_name,
        "action_hash": proposal.action_hash,
        "session_id": proposal.session_id,
        "expires_at": proposal.expires_at.isoformat(),
    }
    from app.domains.sourcing_risk.action_service import create_action_proposal

    return create_action_proposal(
        proposal.run_id,
        action_type,
        payload,
        proposal.idempotency_key,
        candidate_id=candidate_id,
        user_id=proposal.user_id,
        user_role=user_role,
        expected_version=expected_version,
    )


def proposal_from_durable_row(
    row: dict[str, Any],
    *,
    session_id: str,
    user_id: str,
) -> ActionProposal:
    """Rehydrate a Harness proposal without trusting client-supplied arguments."""
    payload = dict(row.get("payload") or {})
    metadata = payload.pop(_HARNESS_METADATA_KEY, None)
    if not isinstance(metadata, dict):
        raise ValueError("持久化提案缺少 Harness 动作绑定元数据")
    tool_name = str(metadata.get("tool_name") or "")
    action_hash = str(metadata.get("action_hash") or "")
    if not tool_name or action_hash != build_action_hash(tool_name, payload):
        raise ValueError("持久化提案动作摘要校验失败")
    expires_at = _parse_datetime(metadata.get("expires_at"))
    return ActionProposal(
        proposal_id=str(row["id"]),
        session_id=session_id,
        run_id=str(row["run_id"]),
        user_id=user_id,
        tool_name=tool_name,
        arguments=payload,
        action_hash=action_hash,
        idempotency_key=str(row["idempotency_key"]),
        expires_at=expires_at,
        status=str(row.get("status") or "pending"),
    )


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("持久化提案缺少过期时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("持久化提案过期时间无效") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


__all__ = ["persist_action_proposal", "proposal_from_durable_row"]
