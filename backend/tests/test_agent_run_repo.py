"""Integration coverage for agent run V2 persistence primitives."""

from collections.abc import Iterator
from contextlib import contextmanager

from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_conn, put_conn
from app.domains.agent_run import repo


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
