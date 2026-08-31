"""Task 9: routing and clarification must consume resolved conversation state."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.api import chat as chat_api
from app.graphs.router import Intent, IntentRouter
from app.services.clarification import detect_clarification_needed


def _collect_events(response) -> list[str]:
    async def collect() -> list[str]:
        return [event async for event in response.body_iterator]

    return asyncio.run(collect())


def test_structured_context_suppresses_company_clarification() -> None:
    result = detect_clarification_needed(
        "分析风险",
        resolved_target_names=["深圳市立创电子有限公司"],
        has_structured_context=True,
    )

    assert result is None


def test_clarification_exposes_explicit_target_field() -> None:
    result = detect_clarification_needed("帮我分析一下风险")

    assert result is not None
    assert result.missing == ["target_supplier_name"]


def test_chat_resolves_context_before_clarification_for_explicit_mode(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        "app.graphs.agent_core.adapter.load_execution_context",
        lambda _sid, _message: calls.append("context") or {
            "history": [],
            "references": [{"name": "深圳市立创电子有限公司"}],
            "conversation_state": {
                "active_suppliers": [{"name": "深圳市立创电子有限公司"}],
                "selected_supplier_names": ["深圳市立创电子有限公司"],
            },
            "current_task": {"target_supplier_names": ["深圳市立创电子有限公司"]},
        },
    )

    async def stream(*_args, execution_context=None):
        assert execution_context is not None
        calls.append("stream")
        yield 'event: done\ndata: {"answer": "ok"}\n\n'

    monkeypatch.setattr(chat_api, "_langgraph_react_stream", stream)
    response = asyncio.run(
        chat_api.chat_stream_endpoint(
            chat_api.ChatRequest(message="分析风险", session_id="context-run", mode="react"),
            SimpleNamespace(state=SimpleNamespace(user_id="")),
        )
    )

    events = _collect_events(response)

    assert calls == ["context", "stream"]
    assert not any(event.startswith("event: clarification") for event in events)


def test_chat_passes_one_execution_context_snapshot_to_selected_graph(monkeypatch) -> None:
    context = {
        "history": [{"role": "assistant", "content": "已找到甲公司"}],
        "references": [{"name": "甲公司", "result_id": "result-1", "candidate_type": "local"}],
        "conversation_state": {"active_suppliers": [{"name": "甲公司"}]},
        "current_task": {"target_supplier_names": ["甲公司"]},
    }
    calls: list[str] = []

    monkeypatch.setattr(
        "app.graphs.agent_core.adapter.load_execution_context",
        lambda _sid, _message: calls.append("context") or context,
    )

    async def stream(_sid, _message, _preferences, execution_context=None):
        calls.append("stream")
        assert execution_context is context
        yield 'event: done\ndata: {"answer": "ok"}\n\n'

    monkeypatch.setattr(chat_api, "_langgraph_react_stream", stream)
    response = asyncio.run(
        chat_api.chat_stream_endpoint(
            chat_api.ChatRequest(message="分析甲公司的风险", session_id="snapshot-run", mode="react"),
            SimpleNamespace(state=SimpleNamespace(user_id="")),
        )
    )

    _collect_events(response)

    assert calls == ["context", "stream"]


def test_chat_rejects_second_active_run_before_loading_context(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.agent_session_guard.acquire_agent_session_run",
        lambda _session_id: None,
    )
    response = asyncio.run(
        chat_api.chat_stream_endpoint(
            chat_api.ChatRequest(message="分析风险", session_id="busy-run", mode="react"),
            SimpleNamespace(state=SimpleNamespace(user_id="")),
        )
    )

    events = _collect_events(response)

    assert any("上一轮仍在处理中" in event for event in events)


def test_router_uses_rules_before_llm_and_never_receives_supplier_targets(monkeypatch) -> None:
    router = IntentRouter()
    llm = object()
    monkeypatch.setattr(router, "_get_llm", lambda: llm)

    assert router.route("帮我找电机供应商") == Intent.SOURCING
