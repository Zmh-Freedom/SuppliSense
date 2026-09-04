"""Task 11 regressions for PostgreSQL-backed Harness execution state."""

from __future__ import annotations

import asyncio

from app.graphs.agent_core import adapter
from app.graphs.streaming import stream_harness_graph


def test_load_execution_context_prefers_postgres_snapshot_over_mongo(
    monkeypatch,
) -> None:
    stored = {
        "history": [{"role": "assistant", "content": "上一轮已找到甲公司"}],
        "references": [{"name": "甲公司有限公司", "supplier_id": "supplier-1"}],
        "conversation_state": {
            "active_suppliers": [{"name": "甲公司有限公司", "supplier_id": "supplier-1"}],
            "selected_supplier_names": ["甲公司有限公司"],
        },
        "current_task": {"task_type": "analysis"},
    }
    monkeypatch.setattr(
        "app.domains.agent_run.state_store.session_state_store.get_execution_context",
        lambda _session_id, _user_id: stored,
    )
    monkeypatch.setattr(
        "app.services.agent._load_conversation_context",
        lambda _session_id: (_ for _ in ()).throw(
            AssertionError("Mongo 不应成为已建控制面会话的执行状态来源")
        ),
    )
    monkeypatch.setattr(
        "app.graphs.agent_core.intent_extractor.extract_conversation_intent",
        lambda *_args: None,
    )

    context = adapter.load_execution_context(
        "00000000-0000-4000-8000-000000000001",
        "分析这家公司的风险",
        "00000000-0000-4000-8000-000000000002",
    )

    assert context["history"] == stored["history"]
    assert context["references"] == stored["references"]
    active_supplier = context["conversation_state"]["active_suppliers"][0]
    assert active_supplier["name"] == "甲公司有限公司"
    assert active_supplier["supplier_id"] == "supplier-1"


def test_harness_stream_uses_control_plane_run_and_persists_snapshot(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []
    updated_contexts: list[dict] = []
    expected_run_id = "00000000-0000-4000-8000-000000000003"
    expected_turn_id = "00000000-0000-4000-8000-000000000004"

    class FakeStore:
        def persist_harness_snapshot(self, run_id, event_type, snapshot):
            calls.append((run_id, event_type, snapshot["run_id"]))

        def update_execution_context(self, _session_id, _user_id, context):
            updated_contexts.append(context)

    monkeypatch.setattr(
        "app.domains.agent_run.state_store.session_state_store",
        FakeStore(),
    )
    monkeypatch.setattr(
        "app.graphs.agent_core.adapter.save_execution_turn",
        lambda *_args: None,
    )

    async def fake_run_harness(state, *, config, persist, **_kwargs):
        assert state["run_id"] == expected_run_id
        assert state["turn_id"] == expected_turn_id
        assert config["configurable"]["thread_id"] == expected_run_id
        await persist("load_session", state)
        return {
            "task_specs": [],
            "tool_outcomes": [],
            "evidence_records": [],
            "evidence_coverage": {
                "required_dimensions": [],
                "covered_dimensions": [],
                "missing_dimensions": [],
            },
            "answer": {
                "status": "needs_review",
                "summary": "证据不足",
                "claims": [],
                "limitations": [],
                "evidence_refs": [],
            },
        }

    monkeypatch.setattr("app.graphs.harness.run_harness", fake_run_harness)
    events = asyncio.run(
        _collect(
            stream_harness_graph(
                "分析甲公司风险",
                "00000000-0000-4000-8000-000000000005",
                {
                    "history": [],
                    "references": [{"name": "甲公司有限公司"}],
                    "conversation_state": {},
                    "current_task": {},
                },
                user_id="00000000-0000-4000-8000-000000000006",
                turn_id=expected_turn_id,
                run_id=expected_run_id,
                run_config={"configurable": {
                    "thread_id": "session-thread-that-must-not-be-reused",
                    "checkpoint_ns": "chat:harness",
                }},
            )
        )
    )

    assert any(event.startswith("event: agent_answer") for event in events)
    assert calls == [(expected_run_id, "load_session", expected_run_id)]
    assert updated_contexts


async def _collect(stream):
    return [event async for event in stream]
