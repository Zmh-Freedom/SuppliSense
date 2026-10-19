"""Live PG lifecycle and action receipts for managed write conversations."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests.test_run_coordinator_integration import run_factory
from app.db.postgres import get_cursor
from app.domains.agent_run.coordinator import ExecutionLost, RunCoordinator
from app.domains.agent_run.repo import get_run, list_events_after

pytestmark = pytest.mark.integration


def test_approval_pause_releases_owner_and_resume_fences_original_attempt(run_factory):
    create, user, _, _ = run_factory
    run = create()
    coordinator = RunCoordinator()
    first = coordinator.claim(run, "first")
    checkpoint = {"checkpoint_id": "checkpoint-a", "digest": "digest-a", "config": {"configurable": {"thread_id": run}}}
    coordinator.pause(first, snapshot={}, checkpoint=checkpoint, reason="waiting_approval", events=[("done", {"status": "waiting_approval"})])
    assert get_run(run)["status"] == "ACTION_PENDING"
    assert coordinator.is_paused(run)
    with pytest.raises(ExecutionLost):
        coordinator.check(first)
    second = coordinator.resume(run, user, checkpoint=checkpoint)
    assert second.fencing_token > first.fencing_token
    assert second.attempt_id != first.attempt_id
    coordinator.check(second)
    with pytest.raises(ExecutionLost):
        coordinator.check(first)
    assert len(list_events_after(run)) == 1


def test_modified_checkpoint_cannot_resume_a_paused_run(run_factory):
    create, user, _, _ = run_factory
    run = create()
    coordinator = RunCoordinator()
    permit = coordinator.claim(run, "worker")
    checkpoint = {"checkpoint_id": "a", "digest": "frozen", "config": {}}
    coordinator.pause(permit, snapshot={}, checkpoint=checkpoint, reason="waiting_approval", events=[])
    with pytest.raises(ExecutionLost):
        coordinator.resume(run, user, checkpoint={**checkpoint, "digest": "changed"})
    assert coordinator.is_paused(run)


def test_concurrent_approval_resume_grants_only_one_new_attempt(run_factory):
    create, user, _, _ = run_factory
    run = create()
    coordinator = RunCoordinator()
    checkpoint = {"checkpoint_id": "a", "digest": "same", "config": {}}
    coordinator.pause(coordinator.claim(run, "old"), snapshot={}, checkpoint=checkpoint, reason="waiting_approval", events=[])
    barrier = Barrier(2)

    def resume(_):
        barrier.wait()
        try:
            return coordinator.resume(run, user, checkpoint=checkpoint)
        except ExecutionLost:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(resume, range(2)))
    assert sum(attempt is not None for attempt in attempts) == 1


def test_read_and_write_chat_share_the_session_single_active_guard(run_factory):
    _, user, session, store = run_factory
    commit = store.start_harness_turn(session, user, "加入监控", run_type="sourcing_risk_v2", state={"execution_context": {}})
    run = str(commit["run"].id)
    assert get_run(run)["run_type"] == "sourcing_risk_v2"
    with pytest.raises(ValueError, match="未结束"):
        store.start_harness_turn(session, user, "分析供应商")
    with get_cursor() as (_, cur):
        cur.execute("SELECT execution_input FROM agent_run_executions WHERE run_id=%s", (run,))
        frozen = cur.fetchone()[0]
    assert frozen["user_message"] == "加入监控"
    assert frozen["runtime"] == "agent-supervisor"


def test_managed_snapshot_cannot_publish_after_cancel(run_factory):
    from app.domains.agent_run.coordinator import execution_scope
    from app.domains.agent_run.service import persist_supervisor_snapshot
    create, user, _, _ = run_factory
    run = create()
    coordinator = RunCoordinator()
    permit = coordinator.claim(run, "worker")
    coordinator.cancel(run, user)
    with execution_scope(permit), pytest.raises(ExecutionLost):
        persist_supervisor_snapshot(run, "COMPLETED", "done", {"final_answer": "late"})
    assert get_run(run)["status"] == "CANCELLED"


def approved_action(run_factory):
    from uuid import uuid4
    from psycopg2.extras import Json
    create, user, _, _ = run_factory
    run, proposal = create(), str(uuid4())
    with get_cursor() as (_, cur):
        cur.execute("UPDATE users SET role='purchaser' WHERE id=%s", (user,))
        cur.execute("INSERT INTO agent_action_proposals(id,run_id,action_type,status,payload,idempotency_key) VALUES(%s,%s,'export_report','approved',%s,%s)", (proposal, run, Json({"company_name": "甲公司"}), proposal))
    return run, user, proposal, {"payload": {"run_id": run, "proposal_id": proposal, "approver_id": user, "managed_execution": True}}


def test_repeated_delivery_returns_the_same_actual_receipt(run_factory, monkeypatch):
    from app.domains.agent_run.coordinator import execution_scope
    from app.domains.sourcing_risk import action_service
    run, user, _, event = approved_action(run_factory)
    calls = []
    monkeypatch.setattr(action_service, "require_v2_execution", lambda _: None)
    monkeypatch.setattr(action_service, "_execute_action", lambda *args: calls.append(args) or {"report_id": "actual-report"})
    with execution_scope(RunCoordinator().claim(run, "worker")):
        first = action_service.execute_sourcing_risk_action(event)
        second = action_service.execute_sourcing_risk_action(event)
    assert first == second
    assert first["result"] == {"report_id": "actual-report"}
    assert len(calls) == 1
    RunCoordinator().cancel(run, user)
    assert action_service.execute_sourcing_risk_action(event) == first


def test_unknown_write_result_is_never_blindly_retried(run_factory, monkeypatch):
    from app.domains.agent_run.coordinator import execution_scope
    from app.domains.sourcing_risk import action_service
    run, _, _, event = approved_action(run_factory)
    calls = []
    monkeypatch.setattr(action_service, "require_v2_execution", lambda _: None)
    def external(*args):
        calls.append(args)
        raise TimeoutError("write response lost")
    monkeypatch.setattr(action_service, "_execute_action", external)
    with execution_scope(RunCoordinator().claim(run, "worker")):
        with pytest.raises(TimeoutError):
            action_service.execute_sourcing_risk_action(event)
        with pytest.raises(ExecutionLost, match="未知"):
            action_service.execute_sourcing_risk_action(event)
    assert len(calls) == 1


def test_outbox_cannot_start_a_graph_owned_write(run_factory, monkeypatch):
    from app.domains.sourcing_risk import action_service
    _, _, _, event = approved_action(run_factory)
    monkeypatch.setattr(action_service, "require_v2_execution", lambda _: None)
    monkeypatch.setattr(action_service, "_execute_action", lambda *args: pytest.fail("Outbox must not bypass ownership"))
    with pytest.raises(ExecutionLost):
        action_service.execute_sourcing_risk_action(event)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision_case", ["approve", "reject", "expired", "tampered", "revoked", "api"])
async def test_persistent_supervisor_pause_restart_and_resume_keeps_run_and_results(run_factory, monkeypatch, decision_case):
    from app.graphs.agent_supervisor import graph as supervisor
    from app.graphs.agent_supervisor.contracts import AgentResult, PlannerTask, TaskPlan
    from app.graphs.agent_supervisor.runtime import stream_managed_supervisor
    from app.graphs.sourcing_risk_v2.checkpointer import get_sourcing_risk_checkpointer, close_sourcing_risk_checkpointer
    from app.domains.sourcing_risk import action_service
    _, user, session, store = run_factory
    with get_cursor() as (_, cur):
        cur.execute("UPDATE users SET role='purchaser' WHERE id=%s", (user,))
    control = store.start_harness_turn(session, user, "生成报告", run_type="sourcing_risk_v2", state={"execution_context": {"current_task": {}, "history": [], "references": [], "conversation_state": {}}})
    run = str(control["run"].id)
    reads, writes = [], []
    monkeypatch.setattr(supervisor, "plan_agent_task", lambda *_: TaskPlan(tasks=[PlannerTask(task_id="sourcing", agent="sourcing")]))
    async def ready(*args):
        reads.append("read")
        return {"sourcing": AgentResult(agent="sourcing", status="completed", summary="已有资料", recommended_actions=[{"action_type": "export_report", "target": {"company_name": "甲公司"}, "reason": "用户请求", "impact": "生成报告"}])}
    monkeypatch.setattr(supervisor, "run_ready_tasks", ready)
    monkeypatch.setattr(action_service, "require_v2_execution", lambda _: None)
    monkeypatch.setattr(action_service, "_execute_action", lambda *args: writes.append(args) or {"report_id": "real-receipt"})
    if decision_case == "expired":
        from datetime import timedelta
        from app.domains.agent_run import service
        monkeypatch.setattr(service, "SUPERVISOR_APPROVAL_TTL", timedelta(seconds=-1))
    saver = await get_sourcing_risk_checkpointer()
    graph = supervisor.build_agent_supervisor_graph(saver)
    try:
        if decision_case == "api":
            from app.api import chat
            frozen = store.get_execution_context(session, user)
            events = [item async for item in chat._langgraph_agent_supervisor_stream(session, "生成报告", execution_context={**frozen, "agent_user_id": user, "_control_plane": {"run_id": run}})]
        else:
            events = [item async for item in stream_managed_supervisor(graph, run, user)]
        assert any('"status": "waiting_approval"' in item for item in events), events
        assert RunCoordinator().is_paused(run)
        assert writes == []
        await close_sourcing_risk_checkpointer()
        saver = await get_sourcing_risk_checkpointer()
        restored_graph = supervisor.build_agent_supervisor_graph(saver)
        if decision_case == "revoked":
            with get_cursor() as (_, cur):
                cur.execute("UPDATE users SET role='viewer' WHERE id=%s", (user,))
        if decision_case == "tampered":
            with get_cursor() as (_, cur):
                cur.execute("UPDATE agent_action_proposals SET payload=jsonb_set(payload,'{company_name}','\"乙公司\"'::jsonb) WHERE run_id=%s", (run,))
        if decision_case == "api":
            from types import SimpleNamespace
            response = await chat.resume_endpoint(chat.ResumeRequest(session_id=session, approved=True), SimpleNamespace(id=user))
            resumed = [item async for item in response.body_iterator]
        else:
            resumed = [item async for item in stream_managed_supervisor(restored_graph, run, user, approved=decision_case != "reject")]
        if decision_case in {"expired", "tampered", "revoked"}:
            assert RunCoordinator().is_paused(run)
            assert writes == []
            assert any('event: error' in item for item in resumed)
            return
        if decision_case == "reject":
            assert writes == []
            assert get_run(run)["status"] == "COMPLETED", resumed
            with get_cursor() as (_, cur):
                cur.execute("SELECT status FROM agent_action_proposals WHERE run_id=%s", (run,))
                assert cur.fetchone()[0] == "rejected"
            return
        assert get_run(run)["status"] == "COMPLETED", resumed
        assert len(reads) == len(writes) == 1
        assert sum('event: done' in item for item in resumed) == 1
        assert 'real-receipt' in str(get_run(run)["requirement"]["action_receipts"])
    finally:
        await close_sourcing_risk_checkpointer()


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_window", ["after_checkpoint", "before_checkpoint"])
async def test_harness_restart_reuses_completed_checkpoint_without_repeating_tools(run_factory, crash_window):
    import asyncio
    from app.graphs.harness.graph import run_harness
    from app.graphs.harness.recovery import stream_harness_recovery
    from app.graphs.checkpoint_recovery import input_digest
    from app.graphs.sourcing_risk_v2.checkpointer import get_sourcing_risk_checkpointer, close_sourcing_risk_checkpointer
    from app.tools.executor import ToolOutcome
    create, user, session, store = run_factory
    run = create()
    coordinator = RunCoordinator()
    permit = coordinator.claim(run, "first-process")
    frozen = coordinator.recovery_record(run, user)["execution_input"]
    calls = []
    class Executor:
        async def execute(self, name, arguments, context):
            calls.append(name)
            return ToolOutcome(call_id="committed-call", tool_name=name, tool_version="test", status="success", data={})
    async def persist(event_type, state):
        await asyncio.to_thread(store.persist_harness_snapshot, run, event_type, state, permit=permit)
    saver = await get_sourcing_risk_checkpointer()
    state = {"run_id": run, "user_id": user, "session_id": session, "turn_id": frozen["turn_id"], "user_message": frozen["user_message"], "execution_context": {}, "input_digest": input_digest(frozen), "task_specs": [{"task_id": "risk", "tool_name": "assess_risk", "arguments": {"company_name": "甲公司"}, "entity_id": "company", "dimension": "risk"}], "budget": {"max_tool_calls": 1, "max_loop_iterations": 0}}
    try:
        async def crash_after_commit(event, snapshot, persisted):
            if event == "execute_ready_tasks" and crash_window == "before_checkpoint":
                raise RuntimeError("simulated process exit after PG commit")
        try:
            await run_harness(state, executor=Executor(), checkpointer=saver, config={"configurable": {"thread_id": run}}, persist=persist, progress=crash_after_commit)
        except RuntimeError as exc:
            assert crash_window == "before_checkpoint" and "simulated process exit" in str(exc)
        assert calls == ["assess_risk"]
        with get_cursor() as (_, cur):
            cur.execute("UPDATE agent_run_executions SET lease_until=NOW()-INTERVAL '1 second' WHERE run_id=%s", (run,))
        assert run in coordinator.reconcile_expired()
        await close_sourcing_risk_checkpointer()
        saver = await get_sourcing_risk_checkpointer()
        events = [item async for item in stream_harness_recovery(run, user, saver, approved=True, claim=None)]
        assert get_run(run)["status"] in {"COMPLETED", "PARTIAL", "NEEDS_REVIEW"}, events
        assert len(calls) == 1
        assert sum('event: done' in item for item in events) == 1
    finally:
        await close_sourcing_risk_checkpointer()


def test_cancel_during_write_preserves_receipt_without_reviving_run(run_factory, monkeypatch):
    from threading import Event
    from app.domains.agent_run.coordinator import execution_scope
    from app.domains.sourcing_risk import action_service
    run, user, _, event = approved_action(run_factory)
    coordinator = RunCoordinator()
    permit = coordinator.claim(run, "writer")
    started, release = Event(), Event()
    monkeypatch.setattr(action_service, "require_v2_execution", lambda _: None)
    def external(*args):
        started.set()
        assert release.wait(3)
        return {"report_id": "late-but-real"}
    monkeypatch.setattr(action_service, "_execute_action", external)
    def execute():
        with execution_scope(permit):
            return action_service.execute_sourcing_risk_action(event)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(execute)
        assert started.wait(3)
        coordinator.cancel(run, user)
        release.set()
        receipt = pending.result(timeout=3)
    assert receipt["result"]["report_id"] == "late-but-real"
    assert get_run(run)["status"] == "CANCELLED"
    assert len([item for item in list_events_after(run) if item["event_type"] == "done"]) == 1


@pytest.mark.asyncio
async def test_supervisor_cancellation_stops_next_provider_in_company_loop(run_factory, monkeypatch):
    from app.domains.agent_run.coordinator import execution_scope
    from app.graphs.agent_supervisor.agents import run_agent_task
    from app.graphs.agent_supervisor.contracts import PlannerTask
    create, user, _, _ = run_factory
    run = create()
    coordinator, calls = RunCoordinator(), []
    permit = coordinator.claim(run, "reader")
    def provider(name):
        calls.append(name)
        coordinator.cancel(run, user)
        return {"score": 90}
    monkeypatch.setattr("app.domains.risk.service.calculate_company_risk_preview", provider)
    with execution_scope(permit), pytest.raises(ExecutionLost):
        await run_agent_task(PlannerTask(task_id="risk", agent="risk"), {"run_id": run, "intent": {"target_supplier_names": ["甲", "乙"]}})
    assert calls == ["甲"]


@pytest.mark.asyncio
async def test_supervisor_renews_lease_during_continuous_progress(run_factory, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.graphs.agent_supervisor import runtime
    _, user, session, store = run_factory
    control = store.start_harness_turn(session, user, "分析", run_type="sourcing_risk_v2", state={"execution_context": {"current_task": {}, "history": [], "references": [], "conversation_state": {}}})
    run = str(control["run"].id)
    class Graph:
        async def astream(self, data, config, **kwargs):
            self.data, self.config = data, config
            for index in range(30):
                await asyncio.sleep(0.002)
                yield {"progress": {"index": index}}
        async def aget_state(self, config):
            return SimpleNamespace(values=self.data, config=self.config)
    heartbeats = []
    original = runtime.run_coordinator.heartbeat
    def heartbeat(permit):
        heartbeats.append(permit)
        return original(permit)
    monkeypatch.setattr(runtime, "HEARTBEAT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(runtime.run_coordinator, "heartbeat", heartbeat)
    events = [item async for item in runtime.stream_managed_supervisor(Graph(), run, user)]
    assert heartbeats, events
    assert get_run(run)["status"] == "COMPLETED"


def test_legacy_paused_supervisor_can_be_cancelled_then_replaced(run_factory):
    from app.domains.agent_run.repo import insert_run
    from app.domains.agent_run.chat_interrupt_repo import save_chat_interrupt, has_chat_interrupt
    from app.domains.agent_run.service import cancel_active_harness_run
    _, user, session, store = run_factory
    store.create_session(user, session_id=session)
    run = insert_run(run_type="sourcing_risk_v2", requirement={"requirement_text": "旧任务"}, user_id=user, status="ACTION_PENDING", session_id=session)
    save_chat_interrupt(session, {"configurable": {"thread_id": session}}, "agent-supervisor", "旧任务", user)
    cancelled = cancel_active_harness_run(session, user, "purchaser")
    assert cancelled and cancelled["id"] == run["id"]
    assert cancelled["status"] == "CANCELLED"
    assert not has_chat_interrupt(session)
    assert store.start_harness_turn(session, user, "新任务")["run"]


def test_run_api_cancel_uses_the_same_terminal_transaction(run_factory):
    from app.domains.agent_run.service import cancel_run
    create, user, _, _ = run_factory
    run = create()
    permit = RunCoordinator().claim(run, "worker")
    before = get_run(run)
    cancel_run(run, before["version"], user, "purchaser")
    with get_cursor() as (_, cur):
        cur.execute("SELECT state FROM agent_run_executions WHERE run_id=%s", (run,))
        assert cur.fetchone()[0] == "CANCELLED"
        cur.execute("SELECT status FROM agent_turns WHERE run_id=%s", (run,))
        assert cur.fetchone()[0] == "cancelled"
    with pytest.raises(ExecutionLost):
        RunCoordinator().check(permit)
