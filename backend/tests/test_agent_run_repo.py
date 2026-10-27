"""Integration coverage for agent run V2 persistence primitives."""

import os
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_conn, put_conn
from app.domains.agent_run import execution_jobs, repo


@pytest.fixture
def isolated_pg(monkeypatch: pytest.MonkeyPatch):
    test_database = os.environ.get("SUPPLISENSE_TEST_PG_DB")
    if not test_database:
        pytest.skip("SUPPLISENSE_TEST_PG_DB 未设置，跳过真实 PostgreSQL 验收")
    if test_database.lower() in {"sra", "tianyancha"}:
        pytest.fail("禁止在共享开发库上运行 Agent Run 集成测试")

    from app.core.config import settings
    from app.db.postgres import close_pool

    if test_database == settings.PG_DB:
        pytest.fail("测试数据库必须显式覆盖当前开发库 PG_DB")
    monkeypatch.setattr(settings, "PG_DB", test_database)
    close_pool()
    ensure_pg_schema()
    yield
    close_pool()


class _RollbackCursor:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        del params
        self.queries.append(query)

    def fetchall(self) -> list[tuple[str]]:
        return [("proposal-id",)]


class _RollbackContext:
    def __init__(self, cursor: _RollbackCursor) -> None:
        self.cursor = cursor

    def __enter__(self) -> tuple[None, _RollbackCursor]:
        return None, self.cursor

    def __exit__(self, *args: object) -> None:
        del args


def test_freeze_pending_proposals_scopes_updates_to_v2_runs(monkeypatch) -> None:
    cursor = _RollbackCursor()
    monkeypatch.setattr(repo, "get_cursor", lambda: _RollbackContext(cursor))

    assert repo.freeze_pending_v2_proposals() == 1

    query = cursor.queries[0]
    assert "FROM agent_runs" in query
    assert "run_type = 'sourcing_risk_v2'" in query
    assert "RETURNING agent_action_proposals.id" in query


def test_rollback_outbox_update_scopes_action_events_to_v2_runs(monkeypatch) -> None:
    from app.domains.outbox import repo as outbox_repo

    cursor = _RollbackCursor()
    monkeypatch.setattr(outbox_repo, "get_cursor", lambda: _RollbackContext(cursor))

    assert outbox_repo.cancel_leased_v2_action_events() == 1

    query = cursor.queries[0]
    assert "agent_runs" in query
    assert "run_type = 'sourcing_risk_v2'" in query
    assert "payload->>'run_id'" in query


def test_rollback_outbox_update_preserves_legacy_action_events(monkeypatch) -> None:
    """Legacy action events remain outside the V2 rollback update scope."""
    from app.domains.outbox import repo as outbox_repo

    cursor = _RollbackCursor()
    monkeypatch.setattr(outbox_repo, "get_cursor", lambda: _RollbackContext(cursor))

    outbox_repo.cancel_leased_v2_action_events()

    query = cursor.queries[0]
    assert "agent_runs.run_type = 'sourcing_risk_v2'" in query
    assert "agent_runs.run_type <> 'sourcing_risk_v2'" not in query


class _EventCursor:
    def __init__(self) -> None:
        self._results = [("run-id",), (1,), ("run-id", 1, 1, "stage", {}, None)]

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        del query, params

    def fetchone(self) -> tuple[object, ...]:
        return self._results.pop(0)

    @property
    def description(self) -> list[tuple[str]]:
        return [
            ("run_id",), ("event_id",), ("version",), ("event_type",),
            ("payload",), ("occurred_at",),
        ]


class _StatusCursor:
    def __init__(self) -> None:
        self.executed = False

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        del query, params
        self.executed = True

    def fetchone(self) -> tuple[object, ...]:
        return ("run-id", "CANCELLED", 2)

    @property
    def description(self) -> list[tuple[str]]:
        return [("id",), ("status",), ("version",)]


def test_append_event_accepts_callers_cursor_without_owning_it():
    """The wrapper must not require a new transaction when a cursor is supplied."""
    event = repo.append_event(
        "run-id", 1, "stage", {"status": "CREATED"}, cur=_EventCursor()
    )
    assert event["event_id"] == 1


def test_update_run_status_accepts_callers_cursor_without_owning_it():
    """The status write must share a supplied cursor instead of opening a transaction."""
    cursor = _StatusCursor()
    updated = repo.update_run_status("run-id", 1, "CANCELLED", cur=cursor)
    assert cursor.executed is True
    assert updated == {"id": "run-id", "status": "CANCELLED", "version": 2}


def test_insert_evidence_persists_company_id_as_a_first_class_column(monkeypatch):
    """Routing company identity through candidate_id would break evidence ownership."""
    captured: dict = {}
    monkeypatch.setattr(
        repo,
        "_insert_returning",
        lambda table, values, json_columns: captured.update(
            table=table, values=values, json_columns=json_columns
        ) or values,
    )

    result = repo.insert_evidence(
        "run-id", "company-id", "sanctions", "provider", {"claim_code": "clear"}
    )

    assert captured["table"] == "agent_evidence"
    assert captured["values"]["company_id"] == "company-id"
    assert "candidate_id" not in captured["values"]
    assert result["company_id"] == "company-id"


def test_snapshot_writes_share_the_caller_transaction(monkeypatch):
    """Opening separate transactions would expose a partial workbench snapshot after a failure."""
    cursor = object()
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(repo, "upsert_candidate", lambda *args, **kwargs: calls.append(("candidate", kwargs.get("cur"))) or {"id": "candidate-1"})
    monkeypatch.setattr(repo, "_upsert_evidence", lambda *args, **kwargs: calls.append(("evidence", args[-1])))
    monkeypatch.setattr(repo, "upsert_evidence_review", lambda *args, **kwargs: calls.append(("review", kwargs.get("cur"))) or {"company_id": "company-1"})
    monkeypatch.setattr(repo, "upsert_decision", lambda *args, **kwargs: calls.append(("decision", kwargs.get("cur"))) or {"id": "decision-1"})

    repo.persist_run_snapshot(
        "run-id",
        candidates=[{"candidate_key": "local:company-1", "company_id": "company-1"}],
        evidence_by_company_id={"company-1": [{"dimension": "sanctions"}]},
        evidence_reviews={"company-1": {"status": "clear"}},
        decisions=[{"candidate_id": "candidate-1", "group": "recommended"}],
        cur=cursor,
    )

    assert calls == [
        ("candidate", cursor),
        ("evidence", cursor),
        ("review", cursor),
        ("decision", cursor),
    ]


def test_evidence_snapshot_retry_reuses_provider_evidence_after_reordering():
    """A list position in the evidence key would duplicate one provider record after a replay reorder."""
    observed_ids_by_source: dict[str, list[str]] = {}
    persisted_rows: dict[str, str] = {}

    class Cursor:
        def execute(self, _query: str, params: tuple[object, ...]) -> None:
            evidence_id = str(params[0])
            source_reference = str(params[6])
            observed_ids_by_source.setdefault(source_reference, []).append(evidence_id)
            persisted_rows[evidence_id] = source_reference

    run_id = "00000000-0000-4000-8000-000000000001"
    for evidence in (
        {
            "dimension": "financial",
            "claim_code": "clear",
            "source_type": "financial_provider",
            "source_reference": "provider-record-99",
        },
        {
            "dimension": "sanctions",
            "claim_code": "clear",
            "source_type": "sanctions_provider",
            "source_reference": "provider-record-42",
        },
        {
            "dimension": "sanctions",
            "claim_code": "clear",
            "source_type": "sanctions_provider",
            "source_reference": "provider-record-42",
        },
    ):
        repo._upsert_evidence(
            run_id,
            "company-id",
            evidence,
            "candidate-id",
            Cursor(),
        )

    assert len(set(observed_ids_by_source["provider-record-42"])) == 1
    assert len(persisted_rows) == 2


@contextmanager
def _real_connection() -> Iterator[tuple[object, object]]:
    conn = get_conn()
    cur = None
    try:
        conn.rollback()
        cur = conn.cursor()
        yield conn, cur
    finally:
        if cur is not None:
            cur.close()
        conn.rollback()
        put_conn(conn)


def _insert_run_for_test() -> dict:
    return repo.insert_run(
        run_type="sourcing_risk_v2",
        requirement={"requirement_text": "测试采购需求"},
    )


def _delete_run(run_id: str) -> None:
    with _real_connection() as (conn, cur):
        cur.execute("DELETE FROM agent_runs WHERE id = %s", (run_id,))
        conn.commit()


@pytest.mark.integration
def test_append_event_assigns_monotonic_event_ids_for_one_run(isolated_pg):
    """Replacing the locked sequence with a constant must be detected."""
    del isolated_pg
    run = _insert_run_for_test()
    try:
        first = repo.append_event(run["id"], 1, "stage", {"status": "CREATED"})
        second = repo.append_event(
            run["id"], 1, "stage", {"status": "POLICY_LOCKED"}
        )
        assert (first["event_id"], second["event_id"]) == (1, 2)
    finally:
        _delete_run(run["id"])


@pytest.mark.integration
def test_append_event_uses_callers_transaction_when_cursor_is_supplied(isolated_pg):
    """A rollback of the business transaction must also remove its event."""
    del isolated_pg
    run = _insert_run_for_test()
    try:
        with _real_connection() as (writer_conn, writer_cur):
            with _real_connection() as (observer_conn, observer_cur):
                repo.append_event(
                    run["id"], 1, "stage", {"status": "CREATED"}, cur=writer_cur
                )
                observer_cur.execute(
                    "SELECT COUNT(*) FROM agent_run_events WHERE run_id = %s",
                    (run["id"],),
                )
                assert observer_cur.fetchone() == (0,)
                writer_conn.rollback()
                observer_conn.rollback()
                observer_cur.execute(
                    "SELECT COUNT(*) FROM agent_run_events WHERE run_id = %s",
                    (run["id"],),
                )
                assert observer_cur.fetchone() == (0,)
    finally:
        _delete_run(run["id"])


@pytest.mark.integration
def test_status_update_and_event_roll_back_with_one_callers_transaction(isolated_pg):
    """A rollback must leave neither the status update nor its event visible."""
    del isolated_pg
    run = _insert_run_for_test()
    try:
        with _real_connection() as (writer_conn, writer_cur):
            repo.update_run_status(run["id"], 1, "CANCELLED", cur=writer_cur)
            repo.append_event(
                run["id"], 2, "stage", {"status": "CANCELLED"}, cur=writer_cur
            )
            writer_conn.rollback()
        with _real_connection() as (_, observer_cur):
            observer_cur.execute(
                "SELECT status, version FROM agent_runs WHERE id = %s", (run["id"],)
            )
            assert observer_cur.fetchone() == ("CREATED", 1)
            observer_cur.execute(
                "SELECT COUNT(*) FROM agent_run_events WHERE run_id = %s", (run["id"],)
            )
            assert observer_cur.fetchone() == (0,)
    finally:
        _delete_run(run["id"])


@pytest.mark.integration
def test_update_run_status_requires_expected_version(isolated_pg):
    """Removing the version predicate must not permit stale status changes."""
    del isolated_pg
    run = _insert_run_for_test()
    try:
        assert repo.update_run_status(run["id"], 99, "CANCELLED") is None
    finally:
        _delete_run(run["id"])


@pytest.mark.integration
def test_update_run_status_increments_version(isolated_pg):
    """A matching version must atomically advance once with the status change."""
    del isolated_pg
    run = _insert_run_for_test()
    try:
        updated = repo.update_run_status(run["id"], 1, "CANCELLED")
        assert updated is not None
        assert (updated["status"], updated["version"]) == ("CANCELLED", 2)
    finally:
        _delete_run(run["id"])


@pytest.mark.integration
def test_insert_approval_decision_rejects_proposal_from_another_run(isolated_pg):
    """Removing the composite foreign key must allow an invalid cross-run approval."""
    del isolated_pg
    first_run = _insert_run_for_test()
    second_run = _insert_run_for_test()
    try:
        proposal = repo.insert_action_proposal(
            first_run["id"], "create_supplier", {}, f"test-{first_run['id']}"
        )
        with _real_connection() as (_, cur):
            cur.execute(
                """
                INSERT INTO agent_approval_decisions (id, run_id, proposal_id, decision)
                VALUES (gen_random_uuid(), %s, %s, 'approved')
                """,
                (second_run["id"], proposal["id"]),
            )
    except Exception as exc:
        assert exc.__class__.__name__ == "ForeignKeyViolation"
    else:
        raise AssertionError("跨 Run 审批记录不应被数据库接受")
    finally:
        _delete_run(first_run["id"])
        _delete_run(second_run["id"])


@pytest.mark.integration
def test_candidate_review_batch_claim_is_fenced_recoverable_and_cancel_safe(isolated_pg):
    """A committed review intent survives worker loss and cannot resume after cancellation."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from uuid import uuid4

    del isolated_pg
    run = _insert_run_for_test()
    user_id = str(uuid4())
    batch_id = str(uuid4())
    try:
        with _real_connection() as (conn, cur):
            cur.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES (%s,%s,%s,'test')",
                (user_id, f"candidate-review-{user_id}", f"{user_id}@example.test"),
            )
            cur.execute(
                "UPDATE agent_runs SET user_id=%s, status='IDENTITY_RESOLVING' WHERE id=%s",
                (user_id, run["id"]),
            )
            repo.insert_candidate_review_event_with_cursor(
                batch_id=batch_id,
                run_id=run["id"],
                actor_id=user_id,
                request_key=f"review-{batch_id}",
                request_hash="a" * 64,
                expected_version=1,
                run_version=1,
                decisions=[],
                response={"run_id": run["id"]},
                resume_status="pending",
                dispatch_status="pending",
                cur=cur,
            )
            conn.commit()

        barrier = Barrier(2)

        def claim(worker_id: str):
            barrier.wait()
            return worker_id, execution_jobs.claim_candidate_review_batch(worker_id, lease_seconds=30)

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim, ["review-worker-a", "review-worker-b"]))
        winner_worker, first = next((worker_id, item) for worker_id, item in claims if item is not None)
        assert sum(item is not None for _, item in claims) == 1
        assert first["fencing_token"] == 1

        with _real_connection() as (conn, cur):
            cur.execute(
                "UPDATE sourcing_candidate_review_events SET lease_until=clock_timestamp() - INTERVAL '1 second' WHERE batch_id=%s",
                (batch_id,),
            )
            conn.commit()
        recovered = execution_jobs.claim_candidate_review_batch("review-worker-restart", lease_seconds=30)
        assert recovered is not None
        assert recovered["attempt_count"] == 2
        assert recovered["fencing_token"] == 2
        assert execution_jobs.heartbeat_candidate_review_batch(
            batch_id, "review-worker-restart", recovered["fencing_token"], lease_seconds=30
        )
        assert not execution_jobs.heartbeat_candidate_review_batch(
            batch_id, winner_worker, first["fencing_token"], lease_seconds=30
        )

        repo.update_run_status(run["id"], 1, "CANCELLED")
        with _real_connection() as (conn, cur):
            cur.execute(
                "UPDATE sourcing_candidate_review_events SET lease_until=clock_timestamp() - INTERVAL '1 second' WHERE batch_id=%s",
                (batch_id,),
            )
            conn.commit()
        cancelled = execution_jobs.claim_candidate_review_batch("review-after-cancel", lease_seconds=30)
        assert cancelled == {"batch_id": batch_id, "run_id": run["id"], "status": "cancelled"}

        with _real_connection() as (_, cur):
            cur.execute("SELECT dispatch_status FROM sourcing_candidate_review_events WHERE batch_id=%s", (batch_id,))
            assert cur.fetchone() == ("cancelled",)
    finally:
        _delete_run(run["id"])
        with _real_connection() as (conn, cur):
            cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
            conn.commit()


@pytest.mark.integration
def test_sourcing_review_interrupt_survives_postgres_checkpointer_restart(isolated_pg, monkeypatch):
    """A review batch resumes the durable LangGraph interrupt after saver recreation."""
    import asyncio
    from typing import TypedDict
    from uuid import uuid4

    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    from app.graphs.sourcing_risk_v2 import checkpointer

    del isolated_pg
    monkeypatch.setattr(checkpointer.settings, "AGENT_RUN_V2_ENABLED", True)
    thread_id = str(uuid4())

    class ReviewState(TypedDict, total=False):
        reviewed: bool

    def review_node(_state: ReviewState) -> dict:
        response = interrupt({"next_action": "identity_review_required"})
        return {"reviewed": bool(response.get("accepted"))}

    graph_builder = StateGraph(ReviewState)
    graph_builder.add_node("identity_review", review_node)
    graph_builder.add_edge(START, "identity_review")
    graph_builder.add_edge("identity_review", END)
    config = {"configurable": {"thread_id": thread_id}}

    async def restart_and_resume():
        await checkpointer.close_sourcing_risk_checkpointer()
        saver = await checkpointer.get_sourcing_risk_checkpointer()
        paused_graph = graph_builder.compile(checkpointer=saver)
        try:
            paused = await paused_graph.ainvoke({}, config)
        finally:
            await checkpointer.close_sourcing_risk_checkpointer()
        assert paused["__interrupt__"][0].value["next_action"] == "identity_review_required"

        restarted_saver = await checkpointer.get_sourcing_risk_checkpointer()
        try:
            restarted_graph = graph_builder.compile(checkpointer=restarted_saver)
            resumed = await restarted_graph.ainvoke(Command(resume={"accepted": True}), config)
            return resumed
        finally:
            await restarted_saver.adelete_thread(thread_id)
            await checkpointer.close_sourcing_risk_checkpointer()

    resumed = asyncio.run(restart_and_resume())
    assert resumed["reviewed"] is True
