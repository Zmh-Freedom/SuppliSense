"""Real PostgreSQL leasing tests for the durable Agent execution job."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token
from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_cursor
from app.domains.agent_run.coordinator import ExecutionLost, RunCoordinator
from app.domains.agent_run.execution_jobs import claim_job, enqueue_job, heartbeat_job
from app.domains.agent_run.repo import list_events_after
from app.domains.agent_run.state_store import SessionStateStore

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def worker_schema():
    if settings.PG_DB != "run_lifecycle_test":
        pytest.skip("requires the isolated run_lifecycle_test PostgreSQL database")
    ensure_pg_schema()


@pytest.fixture
def job_factory(worker_schema):
    user_id, session_id = str(uuid4()), str(uuid4())
    with get_cursor() as (_, cur):
        cur.execute(
            "INSERT INTO users (id, username, email, password_hash) VALUES (%s,%s,%s,'test')",
            (user_id, user_id, f"{user_id}@example.test"),
        )
    store = SessionStateStore()

    def create():
        result = store.start_harness_turn(
            session_id, user_id, "分析甲公司", state={"execution_context": {}}
        )
        run_id = str(result["run"].id)
        enqueue_job(run_id)
        return run_id

    yield create, user_id, session_id
    with get_cursor() as (_, cur):
        cur.execute(
            """DELETE FROM outbox_events WHERE aggregate_id IN
               (SELECT id FROM agent_runs WHERE session_id=%s)""",
            (session_id,),
        )
        cur.execute("DELETE FROM agent_sessions WHERE id=%s", (session_id,))
        cur.execute("DELETE FROM users WHERE id=%s", (user_id,))


def _finish(coordinator, permit, text="完成"):
    return coordinator.finish(
        permit,
        snapshot={"status": "completed", "answer": {"status": "completed", "summary": text}},
        answer_text=text,
        events=[("done", {"status": "completed", "answer": text})],
    )


def test_two_workers_claim_one_job_and_only_one_gets_a_permit(job_factory):
    create, _, _ = job_factory
    run_id = create()
    barrier = Barrier(2)

    def claim(owner):
        barrier.wait()
        return claim_job(owner, lease_seconds=30)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, ["worker-a", "worker-b"]))

    winners = [job for job in claimed if job is not None]
    assert len(winners) == 1
    assert winners[0]["permit"].run_id == run_id
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT status,lease_owner,attempts,attempt_id,fencing_token FROM agent_execution_jobs WHERE run_id=%s",
            (run_id,),
        )
        status, owner, attempts, attempt_id, token = cur.fetchone()
        cur.execute(
            "SELECT state,attempt_id,fencing_token FROM agent_run_executions WHERE run_id=%s",
            (run_id,),
        )
        execution = cur.fetchone()
    assert status == "LEASED"
    assert owner in {"worker-a", "worker-b"}
    assert attempts == 1
    assert execution == ("RUNNING", attempt_id, token)


def test_expired_job_is_taken_over_with_a_new_fencing_token(job_factory):
    create, _, _ = job_factory
    run_id = create()
    first = claim_job("worker-a", lease_seconds=30)
    assert first is not None
    with get_cursor() as (_, cur):
        cur.execute(
            "UPDATE agent_execution_jobs SET lease_until=NOW()-INTERVAL '1 second' WHERE run_id=%s",
            (run_id,),
        )
        cur.execute(
            "UPDATE agent_run_executions SET lease_until=NOW()-INTERVAL '1 second' WHERE run_id=%s",
            (run_id,),
        )
    second = claim_job("worker-b", lease_seconds=30)
    assert second is not None
    assert second["recovery"] is True
    assert second["permit"].fencing_token > first["permit"].fencing_token
    assert second["permit"].attempt_id != first["permit"].attempt_id
    with pytest.raises(ExecutionLost):
        RunCoordinator().check(first["permit"])
    _finish(RunCoordinator(), second["permit"])
    done = [event for event in list_events_after(run_id) if event["event_type"] == "done"]
    assert len(done) == 1
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT status,completed_at FROM agent_execution_jobs WHERE run_id=%s",
            (run_id,),
        )
        status, completed_at = cur.fetchone()
    assert status == "SUCCEEDED" and completed_at is not None


def test_old_worker_cannot_overwrite_after_takeover(job_factory):
    create, _, _ = job_factory
    run_id = create()
    first = claim_job("worker-a", lease_seconds=30)
    assert first is not None
    with get_cursor() as (_, cur):
        cur.execute(
            "UPDATE agent_execution_jobs SET lease_until=NOW()-INTERVAL '1 second' WHERE run_id=%s",
            (run_id,),
        )
        cur.execute(
            "UPDATE agent_run_executions SET lease_until=NOW()-INTERVAL '1 second' WHERE run_id=%s",
            (run_id,),
        )
    second = claim_job("worker-b", lease_seconds=30)
    assert second is not None
    with pytest.raises(ExecutionLost):
        _finish(RunCoordinator(), first["permit"], text="旧 worker 的迟到结果")
    _finish(RunCoordinator(), second["permit"], text="新 worker 的结果")
    done = [event for event in list_events_after(run_id) if event["event_type"] == "done"]
    assert len(done) == 1
    assert done[0]["payload"]["answer"] == "新 worker 的结果"


def test_heartbeat_is_bound_to_the_lease_owner(job_factory):
    create, _, _ = job_factory
    run_id = create()
    claimed = claim_job("worker-a", lease_seconds=30)
    assert claimed is not None
    assert heartbeat_job(claimed["job_id"], "worker-a", lease_seconds=30) is True
    assert heartbeat_job(claimed["job_id"], "worker-b", lease_seconds=30) is False


def test_worker_executes_a_queued_job_and_commits_terminal_events(job_factory, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver

    from app.domains.agent_run.repo import get_run
    from app.workers import agent_worker

    create, _, _ = job_factory
    run_id = create()

    async def fake_checkpointer():
        return MemorySaver()

    async def fake_harness(state, **kwargs):
        return {
            **state,
            "answer": {"status": "completed", "summary": "worker 完成的分析", "claims": []},
            "tool_outcomes": [],
            "evidence_records": [],
        }

    monkeypatch.setattr(agent_worker, "_get_checkpointer", fake_checkpointer)
    monkeypatch.setattr("app.graphs.harness.run_harness", fake_harness)

    result = agent_worker.run_worker_once("worker-test")
    assert result["executed"] == 1
    assert result["terminal"] is True
    run = get_run(run_id)
    assert run["status"] == "COMPLETED"
    events = list_events_after(run_id)
    assert events[-1]["event_type"] == "done"
    assert events[-1]["payload"]["answer"] == "worker 完成的分析"
    with get_cursor() as (_, cur):
        cur.execute("SELECT status,lease_owner,attempts FROM agent_execution_jobs WHERE run_id=%s", (run_id,))
        assert cur.fetchone() == ("SUCCEEDED", None, 1)


def test_worker_takes_over_an_expired_job_and_old_permit_cannot_overwrite(job_factory, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver

    from app.domains.agent_run.repo import get_run
    from app.workers import agent_worker

    create, _, _ = job_factory
    run_id = create()
    first = claim_job("dead-worker", lease_seconds=30)
    assert first is not None
    with get_cursor() as (_, cur):
        cur.execute("UPDATE agent_execution_jobs SET lease_until=NOW()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))
        cur.execute("UPDATE agent_run_executions SET lease_until=NOW()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))

    async def fake_checkpointer():
        return MemorySaver()

    async def fake_harness(state, **kwargs):
        return {
            **state,
            "answer": {"status": "completed", "summary": "接管后的结果", "claims": []},
            "tool_outcomes": [],
            "evidence_records": [],
        }

    monkeypatch.setattr(agent_worker, "_get_checkpointer", fake_checkpointer)
    monkeypatch.setattr("app.graphs.harness.run_harness", fake_harness)

    result = agent_worker.run_worker_once("takeover-worker")
    assert result["executed"] == 1 and result["terminal"] is True
    assert get_run(run_id)["status"] == "COMPLETED"
    with pytest.raises(ExecutionLost):
        _finish(RunCoordinator(), first["permit"], text="旧 worker 迟到的结果")
    done = [event for event in list_events_after(run_id) if event["event_type"] == "done"]
    assert len(done) == 1
    assert done[0]["payload"]["answer"] == "接管后的结果"
    with get_cursor() as (_, cur):
        cur.execute("SELECT status,attempts,recoveries FROM agent_execution_jobs WHERE run_id=%s", (run_id,))
        assert cur.fetchone() == ("SUCCEEDED", 2, 1)


def test_worker_resume_prefers_committed_ledger_over_a_stale_checkpoint(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.graphs.checkpoint_recovery import checkpoint_proof, input_digest
    from app.workers import agent_worker

    frozen = {
        "run_id": "run-1",
        "session_id": "session-1",
        "user_id": "user-1",
        "user_message": "查询风险",
        "runtime": "harness",
    }
    values = {
        **frozen,
        "input_digest": input_digest(frozen),
        "tool_outcomes": [{"task_id": "t1", "tool": "assess", "result": "stale"}],
        "tool_call_count": 1,
        "budget": {"max_llm_calls": 4, "max_tool_calls": 12, "max_loop_iterations": 2, "max_duration_seconds": 120, "max_parallel_tasks": 4},
    }
    checkpoint = SimpleNamespace(
        values=values,
        config={"configurable": {"thread_id": "run-1", "checkpoint_id": "cp-1"}},
    )
    ledger = {
        "tool_outcomes": [{"task_id": "t1", "tool": "assess", "result": "committed"}],
        "tool_call_count": 1,
        "budget": {"max_llm_calls": 4, "max_tool_calls": 12, "max_loop_iterations": 2, "max_duration_seconds": 120, "max_parallel_tasks": 4},
    }

    class FakeGraph:
        def __init__(self):
            self.updated = None

        async def aget_state(self, _config):
            return checkpoint

        async def aupdate_state(self, config, updated):
            self.updated = updated
            return config

    graph = FakeGraph()
    monkeypatch.setattr("app.graphs.harness.graph.build_harness_graph", lambda **_kw: graph)

    record = {
        "execution_input": frozen,
        "snapshot": ledger,
        "checkpoint": checkpoint_proof(checkpoint),
        "reserved_tool_calls": 1,
    }
    claimed = {"recovery": True, "permit": SimpleNamespace(run_id="run-1")}
    consumed = []
    monkeypatch.setattr(
        agent_worker.run_coordinator, "consume_recovery_checkpoint",
        lambda permit, proof: consumed.append((permit, proof)),
    )
    _, resume_state = asyncio.run(
        agent_worker._prepare_resume(claimed, record, object())
    )

    assert resume_state["tool_outcomes"] == ledger["tool_outcomes"]
    assert graph.updated["tool_outcomes"] == ledger["tool_outcomes"]
    assert consumed == [(claimed["permit"], record["checkpoint"])]


def test_cancelled_queued_job_is_not_executed(job_factory, monkeypatch):
    from app.domains.agent_run.repo import get_run
    from app.workers import agent_worker

    create, user_id, _ = job_factory
    run_id = create()
    RunCoordinator().cancel(run_id, user_id)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("cancelled job must never start the graph")

    monkeypatch.setattr("app.graphs.harness.run_harness", forbidden)
    result = agent_worker.run_worker_once("cancel-worker")

    assert result["executed"] == 0
    assert get_run(run_id)["status"] == "CANCELLED"
    with get_cursor() as (_, cur):
        cur.execute("SELECT status FROM agent_execution_jobs WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0] == "CANCELLED"


def test_explicit_resume_releases_a_paused_run_to_the_worker_queue(job_factory):
    create, user_id, _ = job_factory
    run_id = create()
    claimed = claim_job("api-worker", lease_seconds=30)
    assert claimed is not None

    coordinator = RunCoordinator()
    coordinator.pause(
        claimed["permit"],
        snapshot={"status": "waiting_approval"},
        checkpoint={"checkpoint_id": "cp-1", "config": {"configurable": {"thread_id": run_id}}},
        reason="waiting_approval",
        events=[("done", {"status": "paused", "run_id": run_id})],
    )
    with get_cursor() as (_, cur):
        cur.execute("SELECT status FROM agent_execution_jobs WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0] == "PAUSED"

    coordinator.resume_for_worker(run_id, user_id)

    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT state,owner_id,lease_until > clock_timestamp() FROM agent_run_executions WHERE run_id=%s",
            (run_id,),
        )
        assert cur.fetchone() == ("RUNNING", "worker-queue", False)
        cur.execute("SELECT status FROM agent_execution_jobs WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0] == "QUEUED"

    recovered = claim_job("takeover-worker", lease_seconds=30)
    assert recovered is not None
    assert recovered["recovery"] is True
    assert recovered["recoveries"] == 1


def test_worker_setup_failure_commits_a_terminal_run(job_factory, monkeypatch):
    from app.domains.agent_run.repo import get_run
    from app.workers import agent_worker

    create, _, _ = job_factory
    run_id = create()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("checkpoint unavailable")

    monkeypatch.setattr(agent_worker, "_execute_claimed_job", boom)
    result = agent_worker.run_worker_once("fail-worker")

    assert result["executed"] == 1 and result["failed"] is True
    assert get_run(run_id)["status"] == "FAILED"
    with get_cursor() as (_, cur):
        cur.execute("SELECT state FROM agent_run_executions WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0] == "FAILED"
        cur.execute("SELECT status,last_error FROM agent_execution_jobs WHERE run_id=%s", (run_id,))
        status, error = cur.fetchone()
        assert status == "FAILED"
        assert "checkpoint unavailable" in error


def test_recovery_limit_closes_run_turn_and_stream(job_factory):
    from app.domains.agent_run.chat_interrupt_repo import has_chat_interrupt
    from app.domains.agent_run.repo import get_run

    create, _, session_id = job_factory
    run_id = create()
    first = claim_job("worker-a", lease_seconds=30)
    assert first is not None

    with get_cursor() as (_, cur):
        cur.execute(
            """UPDATE agent_execution_jobs
               SET recoveries=max_recoveries, lease_until=clock_timestamp()-INTERVAL '1 second'
               WHERE run_id=%s""",
            (run_id,),
        )
        cur.execute(
            "UPDATE agent_run_executions SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE run_id=%s",
            (run_id,),
        )

    assert claim_job("worker-b", lease_seconds=30) is None

    run = get_run(run_id)
    assert run["status"] == "NEEDS_REVIEW"
    assert run["error_code"] == "AGENT_WORKER_RECOVERY_LIMIT"
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT status,completed_at FROM agent_turns WHERE run_id=%s",
            (run_id,),
        )
        turn_status, completed_at = cur.fetchone()
        cur.execute(
            "SELECT state,pause_reason FROM agent_run_executions WHERE run_id=%s",
            (run_id,),
        )
        execution = cur.fetchone()
        cur.execute(
            "SELECT status,last_error FROM agent_execution_jobs WHERE run_id=%s",
            (run_id,),
        )
        job = cur.fetchone()
    assert turn_status == "failed" and completed_at is not None
    assert execution == ("PAUSED", "recovery_limit_exceeded")
    assert job == ("FAILED", "recovery_limit_exceeded")
    assert not has_chat_interrupt(session_id)
    events = list_events_after(run_id)
    assert any(
        event["event_type"] == "workflow_status"
        and event["payload"].get("status") == "needs_review"
        for event in events
    )
    assert any(
        event["event_type"] == "done"
        and event["payload"].get("status") == "needs_review"
        for event in events
    )


def test_worker_mode_resume_endpoint_requeues_without_running_the_graph(job_factory, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.api.chat import ResumeRequest, _resume_chat_event_generator
    from app.domains.agent_run.chat_interrupt_repo import claim_chat_interrupt

    create, user_id, session_id = job_factory
    run_id = create()
    claimed = claim_job("api-worker", lease_seconds=30)
    assert claimed is not None
    RunCoordinator().pause(
        claimed["permit"],
        snapshot={"status": "waiting_approval"},
        checkpoint={"checkpoint_id": "cp-1", "config": {"configurable": {"thread_id": run_id}}},
        reason="waiting_approval",
        events=[("done", {"status": "paused", "run_id": run_id})],
    )
    paused = claim_chat_interrupt(session_id, user_id)
    assert paused is not None
    claim_token = paused["claim_token"]

    monkeypatch.setattr(settings, "AGENT_EXECUTION_MODE", "worker")

    async def empty_subscription(*_args, **_kwargs):
        if False:
            yield  # pragma: no cover

    async def fake_saver():
        return object()

    monkeypatch.setattr("app.api.chat._stream_durable_harness_run", empty_subscription)
    monkeypatch.setattr(
        "app.graphs.sourcing_risk_v2.checkpointer.get_sourcing_risk_checkpointer",
        fake_saver,
    )
    user = SimpleNamespace(id=user_id, role=SimpleNamespace(value="analyst"))
    request = ResumeRequest(session_id=session_id, approved=True)

    async def consume():
        return [
            event
            async for event in _resume_chat_event_generator(
                request, user, claimed=(paused, claim_token)
            )
        ]

    asyncio.run(consume())

    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT state,owner_id,lease_until > clock_timestamp() FROM agent_run_executions WHERE run_id=%s",
            (run_id,),
        )
        assert cur.fetchone() == ("RUNNING", "worker-queue", False)
        cur.execute("SELECT status FROM agent_execution_jobs WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0] == "QUEUED"


def test_api_created_job_survives_api_restart_and_replays_one_done(job_factory, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver

    from app.graphs.agent_core import adapter
    from app.main import app
    from app.workers import agent_worker

    create, user_id, session_id = job_factory
    placeholder_run = create()
    RunCoordinator().cancel(placeholder_run, user_id)

    monkeypatch.setattr(settings, "AGENT_EXECUTION_MODE", "worker")
    monkeypatch.setattr(
        adapter,
        "load_execution_context",
        lambda *_args, **_kwargs: {
            "session_id": session_id,
            "history": [],
            "references": [],
            "conversation_state": {},
            "current_task": {},
        },
    )

    async def empty_subscription(*_args, **_kwargs):
        if False:
            yield  # pragma: no cover

    async def fake_checkpointer():
        return MemorySaver()

    async def fake_harness(state, **_kwargs):
        return {
            **state,
            "answer": {"status": "completed", "summary": "重启后完成", "claims": []},
            "tool_outcomes": [],
            "evidence_records": [],
        }

    monkeypatch.setattr("app.api.chat._stream_durable_harness_run", empty_subscription)
    monkeypatch.setattr(agent_worker, "_get_checkpointer", fake_checkpointer)
    monkeypatch.setattr("app.graphs.harness.run_harness", fake_harness)
    token = create_access_token({"sub": user_id})

    @asynccontextmanager
    async def no_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "查询供应商风险", "session_id": session_id, "mode": "auto"},
        )
        assert response.status_code == 200, response.text
        with get_cursor() as (_, cur):
            cur.execute(
                """SELECT id FROM agent_runs WHERE session_id=%s AND status='RUNNING'
                   ORDER BY created_at DESC LIMIT 1""",
                (session_id,),
            )
            run_id = str(cur.fetchone()[0])

    # The first API process is gone; the worker claims from PostgreSQL alone.
    result = agent_worker.run_worker_once("restart-worker")
    assert result["terminal"] is True and result["failed"] is False

    with TestClient(app) as client:
        status_response = client.get(
            f"/api/v1/chat/runs/{run_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "COMPLETED"

        replay = client.get(
            f"/api/v1/chat/runs/{run_id}/events",
            headers={"Authorization": f"Bearer {token}", "Last-Event-ID": "0"},
        )
        assert replay.status_code == 200
        assert replay.text.count("event: done") == 1


def test_worker_mode_chat_creates_a_durable_job_and_status_query(job_factory, monkeypatch):
    from app.graphs.agent_core import adapter
    from app.main import app

    create, user_id, session_id = job_factory
    placeholder_run = create()
    RunCoordinator().cancel(placeholder_run, user_id)

    monkeypatch.setattr(settings, "AGENT_EXECUTION_MODE", "worker")
    monkeypatch.setattr(
        adapter,
        "load_execution_context",
        lambda *_args, **_kwargs: {
            "session_id": session_id,
            "history": [],
            "references": [],
            "conversation_state": {},
            "current_task": {},
        },
    )

    async def forbidden_inline(*_args, **_kwargs):
        raise AssertionError("worker mode must not execute the Harness inside the API")
        yield  # pragma: no cover

    async def fake_subscribe(*_args, **_kwargs):
        yield 'event: done\ndata: {"status": "completed", "answer": "stub"}\n\n'

    monkeypatch.setattr("app.api.chat._langgraph_harness_stream", forbidden_inline)
    monkeypatch.setattr("app.api.chat._stream_durable_harness_run", fake_subscribe)
    token = create_access_token({"sub": user_id})

    @asynccontextmanager
    async def no_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "查询供应商风险", "session_id": session_id, "mode": "auto"},
        )
        assert response.status_code == 200, response.text
        assert "event: done" in response.text

        with get_cursor() as (_, cur):
            cur.execute(
                """SELECT id FROM agent_runs WHERE session_id=%s AND status='RUNNING'
                   ORDER BY created_at DESC LIMIT 1""",
                (session_id,),
            )
            run_id = str(cur.fetchone()[0])

        status_response = client.get(
            f"/api/v1/chat/runs/{run_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status_response.status_code == 200, status_response.text
        status = status_response.json()
        assert status["execution"]["state"] == "QUEUED"
        assert status["job"]["status"] == "QUEUED"
        assert status["job"]["attempts"] == 0


def test_worker_takeover_accepts_verified_checkpoint_without_paused_binding(job_factory, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.graphs.checkpoint_recovery import input_digest
    from app.workers import agent_worker
    create, actor, _ = job_factory
    run_id = create()
    first = claim_job('first-owner', lease_seconds=30)
    with get_cursor() as (_, cur):
        cur.execute("UPDATE agent_execution_jobs SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))
        cur.execute("UPDATE agent_run_executions SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))
    claimed = claim_job('new-owner', lease_seconds=30)
    record = RunCoordinator().recovery_record(run_id, actor)
    frozen = record['execution_input']
    values = {**frozen, 'input_digest': input_digest(frozen), 'tool_call_count': 0}
    checkpoint = SimpleNamespace(values=values, config={'configurable': {'thread_id': run_id, 'checkpoint_id': 'saved-before-crash'}})
    class Graph:
        async def aget_state(self, config): return checkpoint
        async def aupdate_state(self, config, values): return config
    monkeypatch.setattr('app.graphs.harness.graph.build_harness_graph', lambda **kw: Graph())
    _, recovered = asyncio.run(agent_worker._prepare_resume(claimed, record, object()))
    assert recovered['run_id'] == run_id
    assert recovered['input_digest'] == input_digest(frozen)
    with pytest.raises(ExecutionLost): RunCoordinator().check(first['permit'])


def test_setup_failure_after_lease_loss_remains_claimable(job_factory, monkeypatch):
    from app.workers import agent_worker
    create, _, _ = job_factory
    run_id = create()
    async def lose_lease(*args):
        with get_cursor() as (_, cur):
            cur.execute("UPDATE agent_execution_jobs SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))
            cur.execute("UPDATE agent_run_executions SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))
        raise RuntimeError('late checkpoint setup failure')
    monkeypatch.setattr(agent_worker, '_execute_claimed_job', lose_lease)
    outcome = agent_worker.run_worker_once('lost-owner')
    assert outcome['terminal'] is False
    with get_cursor() as (_, cur):
        cur.execute('SELECT status FROM agent_execution_jobs WHERE run_id=%s', (run_id,))
        assert cur.fetchone()[0] == 'LEASED'
    successor = claim_job('successor', lease_seconds=30)
    assert successor['recovery'] is True
    _finish(RunCoordinator(), successor['permit'])


def test_pause_proof_is_consumed_before_later_checkpoint_takeover(job_factory):
    import asyncio
    from langgraph.checkpoint.memory import MemorySaver

    from app.graphs.checkpoint_recovery import checkpoint_proof, input_digest
    from app.graphs.harness.graph import build_harness_graph
    from app.workers import agent_worker

    create, actor, _ = job_factory
    run_id = create()
    initial = claim_job("before-pause", lease_seconds=30)
    coordinator = RunCoordinator()
    frozen = coordinator.recovery_record(run_id, actor)["execution_input"]
    values = {**frozen, "input_digest": input_digest(frozen), "tool_call_count": 0}
    saver = MemorySaver()
    graph = build_harness_graph(checkpointer=saver)
    config = {"configurable": {"thread_id": run_id, "checkpoint_ns": ""}}

    async def save_checkpoint(state):
        await graph.aupdate_state(config, state, as_node="load_session")
        return await graph.aget_state(config)

    saved = asyncio.run(save_checkpoint(values))
    coordinator.pause(
        initial["permit"], snapshot={}, checkpoint=checkpoint_proof(saved),
        reason="recovery_review", events=[],
    )
    coordinator.resume_for_worker(run_id, actor)
    resumed = claim_job("after-pause", lease_seconds=30)
    record = coordinator.recovery_record(run_id, actor)
    asyncio.run(agent_worker._prepare_resume(resumed, record, saver))
    assert coordinator.recovery_record(run_id, actor)["checkpoint"] == {}

    advanced = asyncio.run(save_checkpoint({**values, "status": "running"}))
    assert checkpoint_proof(advanced) != checkpoint_proof(saved)
    with get_cursor() as (_, cur):
        cur.execute("UPDATE agent_execution_jobs SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))
        cur.execute("UPDATE agent_run_executions SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE run_id=%s", (run_id,))
    next_attempt = claim_job("after-crash", lease_seconds=30)
    _, state = asyncio.run(agent_worker._prepare_resume(
        next_attempt, coordinator.recovery_record(run_id, actor), saver,
    ))
    assert state["run_id"] == run_id
    assert state["status"] == "running"
    with pytest.raises(ExecutionLost):
        coordinator.consume_recovery_checkpoint(resumed["permit"], checkpoint_proof(saved))
    _finish(coordinator, next_attempt["permit"])
