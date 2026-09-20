"""Deterministic recovery intents for the durable chat control plane."""

from __future__ import annotations

import re


_CANCEL_PATTERN = re.compile(r"(?:停止|取消|终止)(?:刚才|当前|这次|本轮)?(?:正在进行的?|未完成的?)?(?:复核|分析|任务|操作|工作流)?")
# Require a concrete recovery target.  A bare ``继续`` also appears in normal
# sourcing follow-ups such as “结合刚才的候选继续筛选”; routing those turns to
# the durable resume endpoint discards the sourcing requirement and candidate
# snapshot.  Keep the recovery vocabulary explicit and bounded.
_RESUME_PATTERN = re.compile(
    r"(?:继续|恢复|重试|批准|同意)"
    r"(?:(?:刚才|上次|之前)?(?:未完成的?|中断的?|暂停的?|的)?"
    r"(?:主体澄清|分析|复核|任务|操作|工作流|申请|请求|加入监控|移出监控))"
)
_REJECT_PATTERN = re.compile(
    r"(?:拒绝|驳回|不同意)"
    r"(?:(?:刚才|上次|之前)?(?:的)?(?:申请|请求|操作|加入监控|移出监控))"
)


def is_chat_cancel_request(message: str) -> bool:
    """Recognize an explicit user cancellation without using an LLM call."""
    normalized = "".join(str(message or "").split())
    return bool(normalized and _CANCEL_PATTERN.search(normalized))


def is_chat_resume_request(message: str) -> bool:
    """Recognize a durable continuation request without broad fuzzy matching."""
    normalized = "".join(str(message or "").split())
    return bool(normalized and _RESUME_PATTERN.search(normalized))


def is_chat_reject_request(message: str) -> bool:
    """Recognize a natural-language denial of a pending approval."""
    normalized = "".join(str(message or "").split())
    return bool(normalized and _REJECT_PATTERN.search(normalized))


def recovery_message() -> str:
    """Stable user-facing message for a missing durable resume target."""
    return "当前没有可恢复的未完成任务。请重新发起分析，或先完成待确认的主体/审批操作。"


__all__ = [
    "is_chat_cancel_request",
    "is_chat_resume_request",
    "is_chat_reject_request",
    "recovery_message",
]
