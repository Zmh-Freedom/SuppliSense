"""Task 9: routing and clarification must consume resolved conversation state."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

from app.api import chat as chat_api
from app.graphs.router import Intent, IntentRouter
from app.services.clarification import detect_clarification_needed


def _collect_events(response) -> list[str]:
    async def collect() -> list[str]:
        return [event async for event in response.body_iterator]

    return asyncio.run(collect())


def test_all_read_only_chat_modes_use_harness() -> None:
    for mode in ("auto", "harness", "react", "plan-execute", "multi-agent", "sourcing", "parallel"):
        assert chat_api._select_chat_stream(mode, requested_action="none") is chat_api._langgraph_harness_stream


def test_write_action_keeps_durable_approval_entry() -> None:
    assert chat_api._select_chat_stream("auto", requested_action="add_watchlist") is chat_api._langgraph_agent_supervisor_stream
    assert chat_api._select_chat_stream("auto", requested_action="remove_watchlist") is chat_api._langgraph_agent_supervisor_stream
    assert chat_api._select_chat_stream("auto", requested_action="batch_add_watchlist") is chat_api._langgraph_agent_supervisor_stream


def test_supervisor_session_parent_is_created_for_new_uuid_session(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    class FakeSessionStore:
        def get_session(self, session_id):
            calls.append(("get", session_id, ""))
            return None

        def create_session(self, user_id, *, session_id):
            calls.append(("create", session_id, user_id))

    monkeypatch.setattr("app.domains.agent_run.state_store.session_state_store", FakeSessionStore())
    session_id = str(uuid4())
    user_id = str(uuid4())

    chat_api._ensure_agent_session(session_id, user_id)

    assert calls == [("get", session_id, ""), ("create", session_id, user_id)]


def test_chat_stream_converts_supervisor_exception_to_sse_error(monkeypatch) -> None:
    context = {
        "history": [],
        "references": [{"name": "甲公司"}],
        "conversation_state": {"selected_supplier_names": ["甲公司"]},
        "current_task": {"target_supplier_names": ["甲公司"]},
        "llm_intent": {"target_supplier_names": ["甲公司"], "requested_action": "none"},
    }
    monkeypatch.setattr("app.graphs.agent_core.adapter.load_execution_context", lambda *_args: context)

    async def broken_stream(*_args, **_kwargs):
        raise RuntimeError("simulated stream failure")
        yield "unreachable"

    monkeypatch.setattr(chat_api, "_langgraph_harness_stream", broken_stream)
    monkeypatch.setattr("app.services.agent_session_guard.acquire_agent_session_run", lambda _sid: "token")
    monkeypatch.setattr("app.services.agent_session_guard.release_agent_session_run", lambda *_args: None)
    monkeypatch.setattr("app.services.agent_session_guard.renew_agent_session_run", lambda *_args: None)

    response = asyncio.run(
        chat_api.chat_stream_endpoint(
            chat_api.ChatRequest(message="分析甲公司的风险", session_id="stream-error", mode="auto"),
            SimpleNamespace(state=SimpleNamespace(user_id="")),
        )
    )
    events = _collect_events(response)

    assert any(event.startswith("event: error") for event in events)
    assert any("Agent 工作流执行失败，请重试" in event for event in events)


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

    monkeypatch.setattr(chat_api, "_langgraph_harness_stream", stream)
    response = asyncio.run(
        chat_api.chat_stream_endpoint(
            chat_api.ChatRequest(message="分析风险", session_id="context-run", mode="react"),
            SimpleNamespace(state=SimpleNamespace(user_id="")),
        )
    )

    events = _collect_events(response)

    assert calls == ["context", "stream"]
    assert not any(event.startswith("event: clarification") for event in events)


def test_explicit_watchlist_write_bypasses_read_only_clarification(monkeypatch) -> None:
    """Write requests must reach the approval stream even for an external target."""
    calls: list[str] = []
    context = {
        "history": [],
        "references": [],
        "conversation_state": {},
        "current_task": {
            "target_supplier_names": ["赛克瑞浦动力电池系统有限公司"],
            "task_type": "action_draft",
        },
        "llm_intent": {
            "target_supplier_names": ["赛克瑞浦动力电池系统有限公司"],
            "requested_action": "add_watchlist",
        },
    }
    monkeypatch.setattr("app.graphs.agent_core.adapter.load_execution_context", lambda *_args: context)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("写操作不应被只读澄清拦截")

    monkeypatch.setattr("app.services.clarification.formal_supplier_identity_clarification", fail_if_called)
    monkeypatch.setattr("app.services.clarification.review_scope_clarification", fail_if_called)
    monkeypatch.setattr("app.services.clarification.external_assessment_clarification", fail_if_called)
    monkeypatch.setattr("app.services.clarification.detect_clarification_needed", fail_if_called)

    async def stream(*_args, execution_context=None):
        assert execution_context is context
        calls.append("stream")
        yield 'event: approval_required\ndata: {"approval_id":"approval-1"}\n\n'

    monkeypatch.setattr(chat_api, "_langgraph_agent_supervisor_stream", stream)
    monkeypatch.setattr("app.services.agent_session_guard.acquire_agent_session_run", lambda _sid: "token")
    monkeypatch.setattr("app.services.agent_session_guard.release_agent_session_run", lambda *_args: None)
    monkeypatch.setattr("app.services.agent_session_guard.renew_agent_session_run", lambda *_args: None)

    response = asyncio.run(
        chat_api.chat_stream_endpoint(
            chat_api.ChatRequest(
                message="把赛克瑞浦动力电池系统有限公司加入监控清单",
                session_id="watchlist-write-route",
                mode="auto",
            ),
            SimpleNamespace(state=SimpleNamespace(user_id="")),
        )
    )
    events = _collect_events(response)

    assert calls == ["stream"]
    assert any(event.startswith("event: approval_required") for event in events)


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

    monkeypatch.setattr(chat_api, "_langgraph_harness_stream", stream)
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
