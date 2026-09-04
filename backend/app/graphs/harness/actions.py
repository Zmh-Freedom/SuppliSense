"""Fail-closed proposal and approval gate for Harness write actions.

This module is intentionally independent from the HTTP layer. PostgreSQL is
the durable owner of proposals in production; the gate validates the immutable
fields before the caller persists or executes that durable proposal.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.tools.executor import ToolContext, ToolError, ToolExecutor, ToolOutcome


class ActionProposal(BaseModel):
    """Immutable description of a pending side effect."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    action_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=255)
    expires_at: datetime
    status: str = Field(default="pending", pattern="^(pending|approved|rejected|expired|executed)$")


class ActionApprovalToken(BaseModel):
    """Decoded approval claims; never accept an unsigned ad-hoc token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = 1
    proposal_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    action_hash: str = Field(min_length=64, max_length=64)
    approver_id: str = Field(min_length=1)
    expires_at: int = Field(gt=0)


class ActionGate:
    """Create, approve and execute one proposal without bypassing ToolExecutor."""

    def __init__(self, executor: ToolExecutor, *, secret_key: str | None = None) -> None:
        self.executor = executor
        self.secret_key = secret_key if secret_key is not None else settings.SECRET_KEY

    def propose(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
        *,
        ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> ActionProposal:
        definition = self.executor.registry.get(tool_name)
        if definition is None:
            raise ValueError("工具未注册，不能生成操作提案")
        if definition.spec.side_effect != "write" or definition.spec.approval_policy != "required":
            raise ValueError("只允许为需要审批的写工具生成操作提案")
        if not context.session_id or not context.run_id or not context.user_id:
            raise ValueError("操作提案必须绑定 session_id、run_id 和 user_id")
        if ttl_seconds <= 0:
            raise ValueError("操作提案有效期必须为正数")
        action_hash = build_action_hash(tool_name, arguments)
        proposal_id = str(uuid5(NAMESPACE_URL, f"supplisense:harness:{context.run_id}:{action_hash}"))
        current = now or datetime.now(timezone.utc)
        expires_at = current + timedelta(seconds=ttl_seconds)
        return ActionProposal(
            proposal_id=proposal_id,
            session_id=context.session_id,
            run_id=context.run_id,
            user_id=context.user_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            action_hash=action_hash,
            idempotency_key=f"harness:{context.run_id}:{action_hash}",
            expires_at=expires_at,
        )

    def approve(
        self,
        proposal: ActionProposal,
        approver_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[ActionProposal, str]:
        if not approver_id.strip():
            raise ValueError("审批人不能为空")
        if proposal.status != "pending":
            raise ValueError("操作提案当前不可审批")
        current = now or datetime.now(timezone.utc)
        expires_at = _as_utc(proposal.expires_at)
        if current >= expires_at:
            raise ValueError("操作提案已过期")
        token = issue_approval_token(proposal, approver_id, secret_key=self.secret_key, now=current)
        return proposal.model_copy(update={"status": "approved"}), token

    async def execute(
        self,
        proposal: ActionProposal,
        approval_token: str,
        *,
        approver_id: str,
        now: datetime | None = None,
    ) -> ToolOutcome:
        verify_approval_token(
            approval_token,
            proposal,
            approver_id=approver_id,
            secret_key=self.secret_key,
            now=now,
        )
        if proposal.status != "approved":
            return ToolOutcome(
                call_id="proposal-gate",
                tool_name=proposal.tool_name,
                tool_version="unknown",
                status="denied",
                error=ToolError(code="proposal_not_approved", message="操作提案尚未批准"),
            )
        context = ToolContext(
            call_id=proposal.proposal_id,
            session_id=proposal.session_id,
            run_id=proposal.run_id,
            user_id=proposal.user_id,
            approval_actor_id=approver_id,
            approval_proposal_id=proposal.proposal_id,
            approval_secret_key=self.secret_key,
            approval_token=approval_token,
            idempotency_key=proposal.idempotency_key,
        )
        outcome = await self.executor.execute(proposal.tool_name, proposal.arguments, context)
        if outcome.status != "success":
            return outcome
        if not outcome.side_effect_receipt:
            return outcome.model_copy(
                update={
                    "status": "failed",
                    "error": ToolError(
                        code="side_effect_receipt_missing",
                        message="写操作未返回可验证的副作用回执",
                    ),
                }
            )
        return outcome


def build_action_hash(tool_name: str, arguments: dict[str, Any]) -> str:
    """Hash the exact tool and canonical arguments approved by the user."""
    payload = json.dumps(
        {"tool_name": tool_name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def issue_approval_token(
    proposal: ActionProposal,
    approver_id: str,
    *,
    secret_key: str | None = None,
    now: datetime | None = None,
) -> str:
    """Issue a short-lived token after a human approval event."""
    active_secret = secret_key if secret_key is not None else settings.SECRET_KEY
    if not approver_id.strip():
        raise ValueError("审批人不能为空")
    current = now or datetime.now(timezone.utc)
    expires_at = _as_utc(proposal.expires_at)
    if current >= expires_at:
        raise ValueError("操作提案已过期")
    claims = ActionApprovalToken(
        proposal_id=proposal.proposal_id,
        session_id=proposal.session_id,
        run_id=proposal.run_id,
        action_hash=proposal.action_hash,
        approver_id=approver_id,
        expires_at=int(expires_at.timestamp()),
    )
    return _sign_claims(claims, active_secret)


def _sign_claims(claims: ActionApprovalToken, secret_key: str) -> str:
    if not secret_key:
        raise ValueError("审批令牌签名密钥未配置，已拒绝写操作")
    payload = _encode(claims.model_dump(mode="json"))
    signature = hmac.new(secret_key.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return f"h1.{payload}.{_b64(signature)}"


def verify_approval_token(
    token: str,
    proposal: ActionProposal,
    *,
    approver_id: str,
    secret_key: str,
    now: datetime | None = None,
) -> ActionApprovalToken:
    if not secret_key:
        raise ValueError("审批令牌签名密钥未配置，已拒绝写操作")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "h1":
        raise ValueError("审批令牌格式无效")
    payload_part, signature_part = parts[1], parts[2]
    expected = hmac.new(
        secret_key.encode("utf-8"), payload_part.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        actual = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
        payload = json.loads(base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4)))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("审批令牌内容无效") from exc
    if not hmac.compare_digest(actual, expected):
        raise ValueError("审批令牌签名无效")
    claims = ActionApprovalToken.model_validate(payload)
    if claims.proposal_id != proposal.proposal_id:
        raise ValueError("审批令牌与操作提案不匹配")
    if claims.session_id != proposal.session_id or claims.run_id != proposal.run_id:
        raise ValueError("审批令牌与运行上下文不匹配")
    if claims.action_hash != build_action_hash(proposal.tool_name, proposal.arguments):
        raise ValueError("审批令牌与已批准动作不匹配")
    if claims.approver_id != approver_id:
        raise ValueError("审批令牌与当前审批人不匹配")
    current = now or datetime.now(timezone.utc)
    if int(current.timestamp()) >= claims.expires_at:
        raise ValueError("审批令牌已过期")
    return claims


def verify_approval_token_for_action(
    token: str,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    context: ToolContext,
    now: datetime | None = None,
) -> ActionApprovalToken:
    """Verify a token against the exact ToolExecutor call context."""
    if not context.session_id or not context.run_id or not context.user_id:
        raise ValueError("审批执行上下文缺少 session_id、run_id 或 user_id")
    if not context.approval_proposal_id:
        raise ValueError("写操作缺少已持久化的 proposal_id")
    if not context.idempotency_key:
        raise ValueError("写操作缺少幂等键")
    current = now or datetime.now(timezone.utc)
    proposal = ActionProposal(
        proposal_id=context.approval_proposal_id,
        session_id=context.session_id,
        run_id=context.run_id,
        user_id=context.user_id,
        tool_name=tool_name,
        arguments=dict(arguments),
        action_hash=build_action_hash(tool_name, arguments),
        idempotency_key=context.idempotency_key,
        expires_at=current + timedelta(seconds=1),
        status="approved",
    )
    return verify_approval_token(
        token,
        proposal,
        approver_id=context.approval_actor_id or context.user_id,
        secret_key=context.approval_secret_key or settings.SECRET_KEY,
        now=now,
    )


def _encode(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _b64(raw)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


__all__ = [
    "ActionApprovalToken",
    "ActionGate",
    "ActionProposal",
    "build_action_hash",
    "issue_approval_token",
    "verify_approval_token_for_action",
    "verify_approval_token",
]
