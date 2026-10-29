"""Real PostgreSQL races; use the dedicated run_lifecycle_test database."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
from threading import Barrier
from uuid import uuid4

import pytest

from app.core.config import settings
from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_cursor
from app.domains.agent_run.coordinator import ExecutionLost, RunCoordinator
from app.domains.agent_run.repo import get_run, list_events_after
from app.domains.agent_run.state_store import SessionStateStore

pytestmark = pytest.mark.integration


@pytest.fixture
def run_factory():
    if settings.PG_DB != "run_lifecycle_test":
        pytest.skip("requires the isolated run_lifecycle_test PostgreSQL database")
    ensure_pg_schema()
    user_id, session_id = str(uuid4()), str(uuid4())
    with get_cursor() as (_, cur):
        cur.execute(
            "INSERT INTO users (id, username, email, password_hash) VALUES (%s,%s,%s,'test')",
            (user_id, user_id, f"{user_id}@example.test"),
        )
    store = SessionStateStore()

    def create():
        result = store.start_harness_turn(session_id, user_id, "分析甲公司", state={"execution_context": {}})
        return str(result["run"].id)

    yield create, user_id, session_id, store
    with get_cursor() as (_, cur):
        cur.execute("DELETE FROM agent_sessions WHERE id = %s", (session_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


def _finish(coordinator, permit):
    return coordinator.finish(
        permit,
        snapshot={"status": "completed", "answer": {"status": "completed", "summary": "分析完成"}},
        answer_text="分析完成",
        events=[("done", {"status": "completed", "answer": "分析完成"})],
    )


def test_two_owners_cannot_claim_the_same_run(run_factory):
    create, _, _, _ = run_factory
    run_id = create()
    barrier = Barrier(2)
    coordinator = RunCoordinator()

    def claim(owner):
        barrier.wait()
        try:
            return coordinator.claim(run_id, owner)
        except ExecutionLost:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        permits = list(pool.map(claim, ["worker-a", "worker-b"]))
    assert sum(permit is not None for permit in permits) == 1


def test_cancel_and_finish_have_one_immutable_terminal_result(run_factory):
    create, user_id, _, _ = run_factory
    run_id = create()
    coordinator = RunCoordinator()
    permit = coordinator.claim(run_id, "worker")
    barrier = Barrier(2)

    def finish():
        barrier.wait()
        try:
            _finish(coordinator, permit)
        except ExecutionLost:
            pass

    def cancel():
        barrier.wait()
        coordinator.cancel(run_id, user_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(finish), pool.submit(cancel)
        a.result(timeout=10)
        b.result(timeout=10)
    status = get_run(run_id)["status"]
    assert status in {"COMPLETED", "CANCELLED"}
    assert len([e for e in list_events_after(run_id) if e["event_type"] == "done"]) == 1
    with pytest.raises(ExecutionLost):
        coordinator.check(permit)
    assert get_run(run_id)["status"] == status


def test_expired_attempt_cannot_renew_or_write(run_factory):
    create, _, _, _ = run_factory
    run_id = create()
    coordinator = RunCoordinator()
    permit = coordinator.claim(run_id, "worker")
    with get_cursor() as (_, cur):
        cur.execute("UPDATE agent_run_executions SET lease_until = NOW() - INTERVAL '1 second' WHERE run_id=%s", (run_id,))
    assert coordinator.heartbeat(permit) is False
    with pytest.raises(ExecutionLost):
        _finish(coordinator, permit)
    assert list_events_after(run_id) == []


def test_first_turn_creation_is_single_active_even_without_redis(run_factory):
    create, _, _, _ = run_factory
    barrier = Barrier(2)

    def start():
        barrier.wait()
        try:
            return create()
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda _: start(), range(2)))
    assert sum(run is not None for run in runs) == 1


def test_finish_rolls_back_all_state_if_final_event_fails(run_factory, monkeypatch):
    create, _, _, _ = run_factory
    run_id = create()
    coordinator = RunCoordinator()
    permit = coordinator.claim(run_id, "worker")

    def fail(*args, **kwargs):
        raise RuntimeError("event write failed")

    with monkeypatch.context() as patch:
        patch.setattr("app.domains.agent_run.coordinator.append_event_with_cursor", fail)
        with pytest.raises(RuntimeError, match="event write failed"):
            _finish(coordinator, permit)
    assert get_run(run_id)["status"] == "RUNNING"
    assert list_events_after(run_id) == []
    _finish(coordinator, permit)
    assert get_run(run_id)["status"] == "COMPLETED"


def test_cancelled_snapshot_cannot_resurrect_run(run_factory):
    create, user_id, _, store = run_factory
    run_id = create()
    coordinator = RunCoordinator()
    permit = coordinator.claim(run_id, "worker")
    coordinator.cancel(run_id, user_id)
    with pytest.raises(ExecutionLost):
        store.persist_harness_snapshot(run_id, "render_answer", {"status": "completed"}, permit=permit)
    assert get_run(run_id)["status"] == "CANCELLED"


def test_disconnected_real_run_commits_complete_replay(run_factory, monkeypatch):
    create, user_id, session_id, _ = run_factory
    run_id = create()

    async def scenario():
        release, graph_done = asyncio.Event(), asyncio.Event()

        async def graph(state, **kwargs):
            await release.wait()
            graph_done.set()
            return {**state, "answer": {"status": "completed", "summary": "断连后的完整结果"}, "tool_outcomes": []}

        monkeypatch.setattr("app.graphs.harness.run_harness", graph)
        from app.graphs.streaming import stream_harness_graph

        stream = stream_harness_graph("分析", session_id, {}, run_id=run_id, user_id=user_id)
        assert "event: run" in await anext(stream)
        await stream.aclose()
        release.set()
        await asyncio.wait_for(graph_done.wait(), 2)
        for _ in range(100):
            run = await asyncio.to_thread(get_run, run_id)
            if run["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.01)
        assert run["status"] == "COMPLETED"
        events = await asyncio.to_thread(list_events_after, run_id)
        assert events[-1]["event_type"] == "done"
        assert events[-1]["payload"]["answer"] == "断连后的完整结果"
        assert len([event for event in events if event["event_type"] == "agent_answer"]) == 1

    asyncio.run(scenario())


def test_reconciliation_only_pauses_expired_owners(run_factory):
    create, _, _, _ = run_factory
    run_id = create()
    coordinator = RunCoordinator()
    permit = coordinator.claim(run_id, "worker")
    assert run_id not in coordinator.reconcile_expired()
    with get_cursor() as (_, cur):
        cur.execute("UPDATE agent_run_executions SET lease_until=NOW()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))
    assert run_id in coordinator.reconcile_expired()
    with get_cursor() as (_, cur):
        cur.execute("SELECT state,pause_reason FROM agent_run_executions WHERE run_id=%s", (run_id,))
        assert cur.fetchone() == ("PAUSED", "interrupted")
    with pytest.raises(ExecutionLost):
        coordinator.check(permit)


def test_unclaimed_run_can_be_reconciled_after_submitter_exits(run_factory):
    create, _, _, _ = run_factory
    run_id = create()
    with get_cursor() as (_, cur):
        cur.execute("SELECT state FROM agent_run_executions WHERE run_id=%s", (run_id,))
        assert cur.fetchone() == ("QUEUED",)
        cur.execute("UPDATE agent_run_executions SET lease_until=NOW()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))
    assert run_id in RunCoordinator().reconcile_expired()


def test_chat_projection_is_idempotent_in_real_mongo(run_factory):
    if settings.MONGO_DB != "run_lifecycle_test":
        pytest.skip("requires isolated run_lifecycle_test MongoDB")
    from app.db.mongo import get_db
    from app.domains.agent_run.projection import project_chat_completion

    create, _, session_id, _ = run_factory
    run_id = create()
    coordinator = RunCoordinator()
    permit = coordinator.claim(run_id, "worker")
    _finish(coordinator, permit)
    event = {"payload": {"session_id": session_id}}
    collection = get_db()["conversations"]
    collection.create_index("session_id", unique=True)
    try:
        project_chat_completion(event)
        project_chat_completion(event)
        result = collection.find_one({"session_id": session_id})
        assert [message["content"] for message in result["messages"]] == ["分析甲公司", "分析完成"]
        assert collection.count_documents({"session_id": session_id}) == 1
    finally:
        collection.delete_many({"session_id": session_id})


def test_old_projection_does_not_publish_an_active_turn_answer(run_factory):
    if settings.MONGO_DB != "run_lifecycle_test":
        pytest.skip("requires isolated MongoDB")
    from app.db.mongo import get_db
    from app.domains.agent_run.projection import project_chat_completion

    create, _, session_id, store = run_factory
    coordinator = RunCoordinator()
    _finish(coordinator, coordinator.claim(create(), "first"))
    second = create()
    permit = coordinator.claim(second, "second")
    turn_id = get_run(second)["requirement"]["turn_id"]
    store.persist_harness_snapshot(second, "render_answer", {
        "run_id": second, "session_id": session_id, "turn_id": turn_id,
        "status": "completed", "answer": {"status": "completed", "summary": "尚未提交的答案"},
    }, permit=permit)
    collection = get_db()["conversations"]
    try:
        project_chat_completion({"payload": {"session_id": session_id}})
        messages = collection.find_one({"session_id": session_id})["messages"]
        assert [message["content"] for message in messages] == ["分析甲公司", "分析完成"]
    finally:
        collection.delete_many({"session_id": session_id})


def test_first_projection_preserves_mongo_only_legacy_history(run_factory):
    if settings.MONGO_DB != "run_lifecycle_test":
        pytest.skip("requires isolated MongoDB")
    from app.db.mongo import get_db
    from app.domains.agent_run.projection import project_chat_completion

    create, _, session_id, _ = run_factory
    legacy = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答", "references": [{"name": "旧供应商"}]}]
    collection = get_db()["conversations"]
    collection.insert_one({"session_id": session_id, "messages": legacy})
    coordinator = RunCoordinator()
    _finish(coordinator, coordinator.claim(create(), "worker"))
    try:
        for _ in range(2):
            project_chat_completion({"payload": {"session_id": session_id}})
        messages = collection.find_one({"session_id": session_id})["messages"]
        assert messages[:2] == legacy
        assert [message["content"] for message in messages[2:]] == ["分析甲公司", "分析完成"]
    finally:
        collection.delete_many({"session_id": session_id})


def test_chat_follow_up_restores_supplier_context_from_postgres(run_factory, app, monkeypatch):
    """Two chat turns reload only the prior turn committed to the isolated control plane."""
    import json
    from contextlib import asynccontextmanager
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from langchain_core.tools import StructuredTool

    from app.api import chat as chat_api
    from app.core.config import settings
    from app.core.deps import get_current_user
    from app.graphs.agent_core.evidence_ledger import build_evidence_record
    from app.graphs.agent_core import intent_extractor, narrator
    from app.graphs.harness import graph as harness_graph
    from app.tools.registry import ToolPayload, ToolRegistry, ToolSpec

    create, user_id, session_id, store = run_factory
    del create  # This test exercises the public chat route, which creates both turns.
    first_message = "分析验收测试企业有限公司的财务风险"
    follow_up = "那这家供应商的财务情况呢？"
    company_name = "验收测试企业有限公司"
    model_responses = {
        first_message: {
            "target_supplier_names": [company_name],
            "analysis_dimensions": ["financial"],
            "capability": "financial",
            "scope": "single_supplier",
            "task_type": "analysis",
            "requested_action": "none",
            "confidence": 0.98,
            "interpretation_summary": "分析验收测试企业有限公司的财务风险。",
            "field_sources": {
                "target_supplier_names": "explicit",
                "analysis_dimensions": "explicit",
            },
        },
        follow_up: {
            "target_supplier_names": [],
            "analysis_dimensions": ["financial"],
            "capability": "financial",
            "scope": "single_supplier",
            "task_type": "analysis",
            "requested_action": "none",
            "confidence": 0.98,
            "interpretation_summary": "继续查看当前供应商的财务情况。",
            "field_sources": {"analysis_dimensions": "explicit"},
        },
    }
    model_requests = []
    tool_calls = []

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        @staticmethod
        def _create(*, messages, **_kwargs):
            request = json.loads(messages[1]["content"])
            model_requests.append(request)
            response = model_responses[request["current_message"]]
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content=json.dumps(response, ensure_ascii=False)))
            ])

    def query_financials(company_name: str) -> dict:
        tool_calls.append(company_name)
        evidence_id = f"context-baseline-{len(tool_calls)}"
        evidence = build_evidence_record(
            evidence_id=evidence_id,
            entity_id=f"entity:{company_name}",
            dimension="financial",
            provider="isolated-context-baseline",
            source_type="internal",
            payload={"revenue": 1200000, "revenue_unit": "CNY", "fiscal_year": 2025},
            collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ).model_dump(mode="json")
        claim = {
            "claim_id": f"context-baseline-claim-{len(tool_calls)}",
            "entity_id": f"entity:{company_name}",
            "dimension": "financial",
            "statement": f"{company_name} 2025 年营业收入为 120 万元。",
            "value": 1200000,
            "fact_path": "revenue",
            "unit": "CNY",
            "evidence_refs": [evidence_id],
            "confidence": 0.95,
        }
        return {
            "status": "success",
            "company_name": company_name,
            "evidence_records": [evidence],
            "claims": [claim],
        }

    registry = ToolRegistry()
    registry.register(
        StructuredTool.from_function(query_financials, name="query_financials", description="固定只读验收 provider"),
        ToolSpec(
            name="query_financials", capability="financial", side_effect="read",
            approval_policy="none", max_attempts=1, evidence_required=True,
        ),
        output_model=ToolPayload,
    )

    monkeypatch.setattr(settings, "PG_DB", "run_lifecycle_test")
    monkeypatch.setattr(settings, "MONGO_DB", "run_lifecycle_test")
    monkeypatch.setattr(settings, "AGENT_EXECUTION_MODE", "inline")
    monkeypatch.setattr(settings, "LLM_API_KEY", "isolated-fake-model-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(harness_graph, "TOOL_REGISTRY", registry)
    monkeypatch.setattr(narrator, "narrate_answer", lambda answer, _message: answer)
    monkeypatch.setattr(chat_api, "_optional_agent_user_id", lambda _request: user_id)
    monkeypatch.setattr("app.domains.auth.service.get_user_by_id", lambda _uid: SimpleNamespace(
        role=SimpleNamespace(value="purchaser"),
    ))
    monkeypatch.setattr("app.domains.auth.preferences.build_preference_context", lambda _uid: "")
    monkeypatch.setattr("app.services.agent_session_guard.acquire_agent_session_run", lambda _sid: "test-lease")
    monkeypatch.setattr("app.services.agent_session_guard.renew_agent_session_run", lambda *_args: True)
    monkeypatch.setattr("app.services.agent_session_guard.release_agent_session_run", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.formal_supplier_identity_clarification", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.review_scope_clarification", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.external_assessment_clarification", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.detect_clarification_needed", lambda *_args, **_kwargs: None)

    async def no_checkpointer():
        return None

    monkeypatch.setattr("app.graphs.sourcing_risk_v2.checkpointer.get_sourcing_risk_checkpointer", no_checkpointer)

    @asynccontextmanager
    async def isolated_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", isolated_lifespan)
    app.dependency_overrides[get_current_user] = lambda: object()

    def events(response_text: str) -> list[tuple[str, dict]]:
        parsed = []
        for block in response_text.split("\n\n"):
            event = next((line.removeprefix("event: ") for line in block.splitlines() if line.startswith("event: ")), None)
            data = next((line.removeprefix("data: ") for line in block.splitlines() if line.startswith("data: ")), None)
            if event and data:
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    payload = {"value": data}
                parsed.append((event, payload))
        return parsed

    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/v1/chat/stream",
                json={"message": first_message, "session_id": session_id, "mode": "auto"},
            )
            assert first.status_code == 200
            first_events = events(first.text)
            assert next(payload for name, payload in first_events if name == "agent_answer")["status"] == "completed"
            committed_context = store.get_execution_context(session_id, user_id)
            assert committed_context is not None
            assert committed_context["history"][-2]["content"] == first_message
            assert committed_context["conversation_state"]["selected_supplier_names"] == [company_name]
            assert [item["name"] for item in committed_context["references"]] == [company_name]

            second = client.post(
                "/api/v1/chat/stream",
                json={"message": follow_up, "session_id": session_id, "mode": "auto"},
            )
            assert second.status_code == 200
            second_events = events(second.text)
            assert next(payload for name, payload in second_events if name == "agent_answer")["status"] == "completed"
            assert len(model_requests) == 2
            assert model_requests[1]["known_supplier_references"][0]["name"] == company_name
            tool_calls_by_turn = [
                payload["args"]["company_name"]
                for response_events in (first_events, second_events)
                for name, payload in response_events
                if name == "tool_call" and payload["tool"] == "query_financials"
            ]
            assert tool_calls == [company_name, company_name]
            assert tool_calls_by_turn == [company_name, company_name]
            assert model_requests[1]["conversation_context"]["selected_supplier_names"] == [company_name]
            assert model_requests[1]["conversation_context"]["previous_task"]["target_supplier_names"] == [company_name]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        with get_cursor() as (_, cur):
            cur.execute(
                """DELETE FROM outbox_events
                   WHERE aggregate_type='agent_run'
                     AND aggregate_id IN (SELECT id FROM agent_runs WHERE session_id=%s)""",
                (session_id,),
            )
