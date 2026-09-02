"""Live PostgreSQL checks for the Agent Harness control plane."""

from collections.abc import Iterator
from contextlib import contextmanager

from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_conn, put_conn
from app.domains.agent_run.state_store import SessionStateStore
from app.graphs.agent_core.entity_memory import resolve_turn


@contextmanager
def _connection() -> Iterator[tuple[object, object]]:
    connection = get_conn()
    cursor = connection.cursor()
    try:
        connection.rollback()
        yield connection, cursor
    finally:
        cursor.close()
        connection.rollback()
        put_conn(connection)


def test_harness_control_plane_persists_session_turn_run_and_detects_stale_version() -> None:
    ensure_pg_schema()
    store = SessionStateStore()
    session = store.create_session()
    try:
        turn_commit = store.append_turn(
            str(session.id),
            expected_version=session.version,
            user_message="找工业相机供应商",
        )
        assert turn_commit is not None
        assert turn_commit.session.version == 2

        assert store.update_session_state(
            str(session.id),
            expected_version=session.version,
            state={"focus_set": ["supplier-1"]},
        ) is None

        run = store.create_run(str(session.id), state={"task_type": "sourcing"})
        assert run.run_type == "agent_harness"
        assert run.session_id == session.id

        memory = resolve_turn(
            "对深圳市立创电子有限公司做风险分析",
            session_id=str(session.id),
            references=[{"name": "深圳市立创电子有限公司", "supplier_id": "supplier-1"}],
        ).memory
        persisted = store.upsert_entity_memory(
            str(session.id),
            expected_version=turn_commit.session.version,
            memory=memory,
        )
        assert persisted is not None
        assert persisted.version == 3
        assert persisted.state["focus_set"]["entity_ids"]
    finally:
        with _connection() as (connection, cursor):
            cursor.execute("DELETE FROM agent_sessions WHERE id = %s", (str(session.id),))
            connection.commit()
