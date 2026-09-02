"""Regression coverage for ReAct approval interruption over SSE."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from app.graphs.streaming import stream_react_graph


class _State(TypedDict):
    messages: Annotated[list, add_messages]
    conversation_state: dict
    current_task: dict


@tool
def _approval_tool() -> dict:
    """Pause the graph for a synthetic approval request."""
    return interrupt({
        "type": "approval",
        "tool": "select_sourcing_result",
        "args": {"result_id": "local-result-1", "action": "apply_access"},
        "message": "确认操作：确认寻源结果操作: apply_access？",
    })


def _build_interrupt_graph():
    async def agent(_state: _State):
        return {"messages": [AIMessage(content="", tool_calls=[{
            "name": "_approval_tool",
            "args": {},
            "id": "approval-call",
            "type": "tool_call",
        }])]}

    def route(state: _State):
        return "tools" if state["messages"][-1].tool_calls else END

    graph = StateGraph(_State)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode([_approval_tool]))
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=MemorySaver())


def test_react_stream_maps_tool_interrupt_to_approval_required_sse(monkeypatch) -> None:
    session_id = "react-interrupt-sse-test"
    stored: dict = {}
    monkeypatch.setattr(
        "app.domains.agent_run.chat_interrupt_repo.save_chat_interrupt",
        lambda session, config, mode, user_message: stored.update(
            session=session, config=config, mode=mode, user_message=user_message
        ),
    )

    async def collect() -> list[str]:
        return [
            event async for event in stream_react_graph(
                _build_interrupt_graph(),
                "对深圳市康斯得电子有限公司执行准入申请",
                session_id,
                run_config={"configurable": {"thread_id": session_id}},
            )
        ]

    events = asyncio.run(collect())
    approval_event = next(event for event in events if event.startswith("event: approval_required"))
    payload = json.loads(approval_event.split("data: ", 1)[1])

    assert payload["tool"] == "select_sourcing_result"
    assert payload["args"]["result_id"] == "local-result-1"
    assert payload["requires_human_approval"] is True
    assert stored["session"] == session_id
    assert stored["mode"] == "react"
