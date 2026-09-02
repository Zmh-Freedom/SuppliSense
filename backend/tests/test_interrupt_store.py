"""Durable-first approval recovery compatibility tests."""

from app.graphs import interrupt_store


def test_interrupt_store_merges_durable_metadata_with_in_process_graph(monkeypatch):
    durable = {
        "session_id": "session-1",
        "config": {"configurable": {"thread_id": "run-1"}},
        "mode": "react",
        "user_message": "确认加入监控",
    }
    monkeypatch.setattr(
        "app.domains.agent_run.chat_interrupt_repo.save_chat_interrupt",
        lambda *_args, **_kwargs: None,
    )
    durable_rows = iter([durable, None])
    monkeypatch.setattr(
        "app.domains.agent_run.chat_interrupt_repo.take_chat_interrupt",
        lambda _session_id: next(durable_rows),
    )
    graph = object()

    interrupt_store.store(
        "session-1",
        graph,
        {"configurable": {"thread_id": "run-1"}},
        "react",
        "确认加入监控",
    )
    restored = interrupt_store.pop("session-1")

    assert restored is not None
    assert restored["graph"] is graph
    assert restored["config"] == durable["config"]
    assert interrupt_store.pop("session-1") is None
