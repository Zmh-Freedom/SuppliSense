"""Integration coverage for agent run V2 persistence primitives."""

from collections.abc import Iterator
from contextlib import contextmanager

from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_conn, put_conn
from app.domains.agent_run import repo


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
    monkeypatch.setattr(repo, "upsert_evidence_review", lambda *args, **kwargs: calls.append(("review", kwargs.get("cur"))) or {"company_id": "company-1"})
    monkeypatch.setattr(repo, "upsert_decision", lambda *args, **kwargs: calls.append(("decision", kwargs.get("cur"))) or {"id": "decision-1"})

    repo.persist_run_snapshot(
        "run-id",
        candidates=[{"candidate_key": "local:company-1", "company_id": "company-1"}],
        evidence_reviews={"company-1": {"status": "clear"}},
        decisions=[{"candidate_id": "candidate-1", "group": "recommended"}],
        cur=cursor,
    )

    assert calls == [("candidate", cursor), ("review", cursor), ("decision", cursor)]


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


def test_append_event_assigns_monotonic_event_ids_for_one_run():
    """Replacing the locked sequence with a constant must be detected."""
    ensure_pg_schema()
    run = _insert_run_for_test()
    try:
        first = repo.append_event(run["id"], 1, "stage", {"status": "CREATED"})
        second = repo.append_event(
            run["id"], 1, "stage", {"status": "POLICY_LOCKED"}
        )
        assert (first["event_id"], second["event_id"]) == (1, 2)
    finally:
        _delete_run(run["id"])


def test_append_event_uses_callers_transaction_when_cursor_is_supplied():
    """A rollback of the business transaction must also remove its event."""
    ensure_pg_schema()
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


def test_status_update_and_event_roll_back_with_one_callers_transaction():
    """A rollback must leave neither the status update nor its event visible."""
    ensure_pg_schema()
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


def test_update_run_status_requires_expected_version():
    """Removing the version predicate must not permit stale status changes."""
    ensure_pg_schema()
    run = _insert_run_for_test()
    try:
        assert repo.update_run_status(run["id"], 99, "CANCELLED") is None
    finally:
        _delete_run(run["id"])


def test_update_run_status_increments_version():
    """A matching version must atomically advance once with the status change."""
    ensure_pg_schema()
    run = _insert_run_for_test()
    try:
        updated = repo.update_run_status(run["id"], 1, "CANCELLED")
        assert updated is not None
        assert (updated["status"], updated["version"]) == ("CANCELLED", 2)
    finally:
        _delete_run(run["id"])


def test_insert_approval_decision_rejects_proposal_from_another_run():
    """Removing the composite foreign key must allow an invalid cross-run approval."""
    ensure_pg_schema()
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
