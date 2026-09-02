"""Deterministic end-to-end Agent workflows executed in CI without external services."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from app.api import chat as chat_api
from app.graphs import react_graph
from app.graphs.streaming import stream_react_graph


pytestmark = pytest.mark.agent_e2e


class _FinalAnswerLLM:
    async def ainvoke(self, _messages):
        return AIMessage(content="准入申请已创建，等待后续处理。")


@tool
def select_sourcing_result(result_id: str, action: str = "watchlist") -> dict:
    """Synthetic admission tool used to verify approval pause and resume."""
    decision = interrupt({
        "tool": "select_sourcing_result",
        "args": {"result_id": result_id, "action": action},
        "message": "确认操作：确认寻源结果操作: apply_access？",
    })
    if not decision.get("approved"):
        return {"cancelled": True}
    return {"success": True, "application_id": "application-e2e-1"}


def _collect(stream) -> list[str]:
    async def collect() -> list[str]:
        return [event async for event in stream]

    return asyncio.run(collect())


def _event_payload(events: list[str], event_name: str) -> dict:
    event = next(event for event in events if event.startswith(f"event: {event_name}"))
    return json.loads(event.split("data: ", 1)[1])


def test_admission_scope_is_reported_without_write_or_approval(monkeypatch) -> None:
    monkeypatch.setattr(react_graph, "TOOLS_LIST", [])
    monkeypatch.setattr(react_graph, "_get_llm", lambda: _FinalAnswerLLM())
    session_id = "agent-e2e-admission-scope"
    graph = react_graph.build_react_graph(checkpointer=MemorySaver())
    context = {
        "conversation_state": {
            "active_suppliers": [{
                "name": "深圳市康斯得电子有限公司",
                "candidate_type": "local",
                "result_id": "local-result-e2e-1",
            }],
            "selected_supplier_names": ["深圳市康斯得电子有限公司"],
        },
        "current_task": {"task_type": "action_draft"},
    }

    events = _collect(stream_react_graph(
        graph,
        "对深圳市康斯得电子有限公司进行准入申请",
        session_id,
        run_config={"configurable": {"thread_id": session_id}},
        execution_context=context,
    ))

    lifecycle = [
        _event_payload([event], "workflow_status")["status"]
        for event in events if event.startswith("event: workflow_status")
    ]
    assert lifecycle == ["running", "completed"]
    assert not any(event.startswith("event: approval_required") for event in events)
    answer = _event_payload(events, "done")["answer"]
    assert "仅支持供应商推荐和加入风险监控" in answer
    assert "供应商管理系统" in answer
    # No approval was requested; the PG-only resume store must remain untouched.


def test_react_stream_emits_completed_workflow_status(monkeypatch) -> None:
    monkeypatch.setattr(react_graph, "TOOLS_LIST", [])
    monkeypatch.setattr(react_graph, "_get_llm", lambda: _FinalAnswerLLM())
    session_id = "agent-e2e-status-completed"
    graph = react_graph.build_react_graph(checkpointer=MemorySaver())

    events = _collect(stream_react_graph(
        graph,
        "查看当前任务状态",
        session_id,
        run_config={"configurable": {"thread_id": session_id}},
        execution_context={"conversation_state": {}, "current_task": {}},
    ))

    statuses = [
        _event_payload([event], "workflow_status")["status"]
        for event in events if event.startswith("event: workflow_status")
    ]
    assert statuses == ["running", "completed"]
    assert any(event.startswith("event: done") for event in events)


def test_chat_endpoint_reuses_one_context_snapshot_for_sourcing_mode(monkeypatch) -> None:
    monkeypatch.setattr(chat_api.settings, "DEBUG", True)
    monkeypatch.setattr(chat_api.settings, "AGENT_CHAT_LEGACY_COMPAT_ENABLED", True)
    context = {
        "history": [],
        "references": [{"name": "甲电机有限公司"}],
        "conversation_state": {"active_suppliers": [{"name": "甲电机有限公司"}]},
        "current_task": {"task_type": "sourcing"},
    }
    calls: list[str] = []
    monkeypatch.setattr(
        "app.graphs.agent_core.adapter.load_execution_context",
        lambda _session_id, _message: calls.append("load") or context,
    )

    async def stream(_session_id, _message, _preferences, execution_context=None):
        calls.append("stream")
        assert execution_context is context
        yield 'event: done\ndata: {"answer": "已完成"}\n\n'

    monkeypatch.setattr(chat_api, "_langgraph_sourcing_stream", stream)
    response = asyncio.run(chat_api.chat_stream_endpoint(
        chat_api.ChatRequest(message="推荐电机供应商", session_id="agent-e2e-context", mode="sourcing"),
        SimpleNamespace(state=SimpleNamespace(user_id="")),
    ))
    events = _collect(response.body_iterator)

    assert calls == ["load", "stream"]
    assert _event_payload(events, "done") == {"answer": "已完成"}
