"""The synchronous conversation loader must leave the event loop free."""

import asyncio
import threading

from app.api import chat
from app.graphs.agent_core import adapter


def test_chat_context_loader_runs_off_event_loop(monkeypatch) -> None:
    caller_thread = threading.get_ident()
    observed: list[int] = []

    def fake_loader(_session_id: str, _message: str, _user_id: str) -> dict:
        observed.append(threading.get_ident())
        return {"history": [], "references": [], "conversation_state": {}, "current_task": {}}

    monkeypatch.setattr(adapter, "load_execution_context", fake_loader)

    async def run() -> dict:
        return await chat._load_stream_execution_context(
            "11111111-1111-4111-8111-111111111111",
            "你好",
            "22222222-2222-4222-8222-222222222222",
        )

    result = asyncio.run(run())

    assert result["history"] == []
    assert observed and observed[0] != caller_thread
