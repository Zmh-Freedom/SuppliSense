"""Unit contracts for deterministic chat cancellation and recovery intents."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.domains.agent_run.chat_recovery import (
    is_chat_cancel_request,
    is_chat_reject_request,
    is_chat_resume_request,
    recovery_message,
)
from app.api import chat as chat_api


def test_chat_recovery_intents_accept_common_user_phrasings() -> None:
    assert is_chat_cancel_request("停止刚才正在进行的复核。")
    assert is_chat_cancel_request("取消本轮分析")
    assert is_chat_resume_request("继续刚才未完成的主体澄清")
    assert is_chat_resume_request("刷新后恢复刚才的分析")
    assert is_chat_resume_request("批准刚才的加入监控申请")
    assert is_chat_reject_request("拒绝刚才的移出监控申请")


def test_chat_recovery_intents_do_not_capture_normal_business_questions() -> None:
    assert not is_chat_cancel_request("查看当前监控清单")
    assert not is_chat_resume_request("分析上海海拉电子有限公司的风险")
    assert not is_chat_resume_request("请结合刚才的工业相机候选继续筛选")
    assert not is_chat_resume_request("继续优化预算和交付约束")
    assert not is_chat_reject_request("拒绝这家供应商")


def test_recovery_message_is_structured_and_actionable() -> None:
    message = recovery_message()
    assert "没有可恢复" in message
    assert "重新发起分析" in message


def test_missing_durable_resume_is_a_structured_sse_terminal(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.domains.agent_run.chat_interrupt_repo.claim_chat_interrupt",
        lambda *_args, **_kwargs: None,
    )

    async def collect() -> list[str]:
        return [
            event
            async for event in chat_api._resume_chat_event_generator(
                chat_api.ResumeRequest(session_id="resume-session"),
                SimpleNamespace(id="user-1"),
            )
        ]

    events = asyncio.run(collect())
    assert any('event: workflow_status\ndata: {"status": "needs_review"' in event for event in events)
    assert any('"status": "needs_review"' in event and "没有可恢复" in event for event in events)
