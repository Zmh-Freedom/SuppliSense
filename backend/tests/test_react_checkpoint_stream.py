import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from app.graphs.streaming import stream_react_graph
from app.graphs.chat_checkpoint import chat_checkpoint_config


class _GraphWithState:
    def __init__(self, messages: list) -> None:
        self._messages = messages
        self.input_data = None
        self.stream_called = False

    async def aget_state(self, _config):
        return SimpleNamespace(values={"messages": self._messages})

    async def astream_events(self, input_data, **_kwargs):
        self.input_data = input_data
        self.stream_called = True
        if False:  # pragma: no cover - keeps this an async generator
            yield None


def _execution_context() -> dict:
    return {
        "conversation_state": {"active_suppliers": []},
        "current_task": {},
    }


def test_chat_checkpoint_config_isolates_graph_namespaces() -> None:
    assert chat_checkpoint_config("same-session", "react") == {
        "configurable": {
            "thread_id": "same-session",
            "checkpoint_ns": "chat:react",
        }
    }
    assert chat_checkpoint_config("same-session", "sourcing") != chat_checkpoint_config(
        "same-session", "react"
    )


def test_react_stream_uses_only_new_message_when_checkpoint_exists() -> None:
    graph = _GraphWithState([HumanMessage(content="已持久化的旧问题")])

    async def collect() -> list[str]:
        return [event async for event in stream_react_graph(
            graph,
            "当前问题",
            "checkpoint-stream",
            history=[{"role": "user", "content": "Mongo 历史问题"}],
            run_config={"configurable": {"thread_id": "checkpoint-stream"}},
            execution_context=_execution_context(),
        )]

    asyncio.run(collect())

    messages = graph.input_data["messages"]
    assert any(isinstance(message, HumanMessage) and message.content == "当前问题" for message in messages)
    assert not any(
        isinstance(message, HumanMessage) and message.content == "Mongo 历史问题"
        for message in messages
    )


def test_react_stream_refuses_unresolved_tool_calls_before_model_invocation() -> None:
    graph = _GraphWithState([AIMessage(content="", tool_calls=[{
        "name": "assess_risk",
        "args": {"company_name": "供应商 A"},
        "id": "unfinished-call",
        "type": "tool_call",
    }])])

    async def collect() -> list[str]:
        return [event async for event in stream_react_graph(
            graph,
            "继续分析",
            "checkpoint-stream",
            run_config={"configurable": {"thread_id": "checkpoint-stream"}},
            execution_context=_execution_context(),
        )]

    events = asyncio.run(collect())

    assert any("未完成的工具执行" in event for event in events)
    assert graph.stream_called is False
