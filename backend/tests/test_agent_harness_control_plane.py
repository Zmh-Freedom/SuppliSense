"""Unit contracts for the Agent Harness PostgreSQL control plane."""

from datetime import datetime, timezone
from uuid import UUID

from app.db import init_pg
from app.domains.agent_run import harness_contracts
from app.domains.agent_run import state_store


SESSION_ID = "00000000-0000-4000-8000-000000000001"
RUN_ID = "00000000-0000-4000-8000-000000000002"
NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _session_row(version: int = 1) -> tuple[object, ...]:
    return (SESSION_ID, None, "active", version, {}, NOW, NOW)


def _session_columns() -> list[tuple[str]]:
    return [
        ("id",),
        ("user_id",),
        ("status",),
        ("version",),
        ("state",),
        ("created_at",),
        ("updated_at",),
    ]


class _Cursor:
    def __init__(self, results: list[tuple[list[tuple[str]], tuple[object, ...] | None]]) -> None:
        self.results = results
        self.statements: list[tuple[str, object]] = []
        self._description: list[tuple[str]] = []

    @property
    def description(self) -> list[tuple[str]]:
        return self._description

    def execute(self, query: str, params: object = ()) -> None:
        self.statements.append((query, params))
        if self.results:
            self._description, _ = self.results[0]

    def fetchone(self) -> tuple[object, ...] | None:
        description, row = self.results.pop(0)
        self._description = description
        return row


class _CursorContext:
    def __init__(self, cursor: _Cursor) -> None:
        self.cursor = cursor

    def __enter__(self) -> tuple[None, _Cursor]:
        return None, self.cursor

    def __exit__(self, *args: object) -> None:
        del args


def test_harness_contracts_are_serializable_and_separate_control_plane_concepts() -> None:
    session = harness_contracts.AgentSession(id=UUID(SESSION_ID), state={"focus_set": ["supplier-1"]})
    run = harness_contracts.AgentRun(
        id=UUID(RUN_ID),
        session_id=UUID(SESSION_ID),
        run_type="agent_harness",
        status="created",
        state={"tasks": []},
    )
    proposal = harness_contracts.AgentActionProposal(
        id=UUID(RUN_ID),
        run_id=UUID(RUN_ID),
        action_type="add_watchlist",
        idempotency_key="proposal-1",
    )

    assert session.model_dump(mode="json")["id"] == SESSION_ID
    assert run.model_dump(mode="json")["run_type"] == "agent_harness"
    assert proposal.model_dump(mode="json")["status"] == "pending"


def test_control_plane_schema_defines_session_turn_task_entity_and_tool_call_tables() -> None:
    ddl = "\n".join(init_pg.DDL_STATEMENTS)
    indexes = "\n".join(init_pg.INDEX_STATEMENTS)

    for table in (
        "agent_sessions",
        "agent_turns",
        "agent_tasks",
        "agent_entities",
        "agent_tool_calls",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    assert "session_id UUID REFERENCES agent_sessions(id) ON DELETE CASCADE" in ddl
    assert "UNIQUE (session_id, turn_number)" in ddl
    assert "UNIQUE (session_id, entity_key)" in ddl
    assert "idx_agent_sessions_user_updated" in indexes
    assert "idx_agent_tool_calls_run_created" in indexes


def test_update_session_state_uses_optimistic_version_and_returns_no_row_on_conflict(monkeypatch) -> None:
    cursor = _Cursor([(_session_columns(), None)])
    monkeypatch.setattr(state_store, "get_cursor", lambda: _CursorContext(cursor))

    result = state_store.SessionStateStore().update_session_state(
        SESSION_ID,
        expected_version=4,
        state={"focus_set": ["supplier-1"]},
    )

    assert result is None
    query, params = cursor.statements[0]
    assert "version = version + 1" in query
    assert "WHERE id = %s AND version = %s" in query
    assert params[-2:] == [SESSION_ID, 4]


def test_append_turn_locks_session_and_advances_session_version_atomically(monkeypatch) -> None:
    turn_columns = [
        ("id",),
        ("session_id",),
        ("turn_number",),
        ("user_message",),
        ("assistant_message",),
        ("status",),
        ("request",),
        ("response",),
        ("run_id",),
        ("created_at",),
        ("completed_at",),
    ]
    turn_row = (
        "00000000-0000-0000-0000-000000000003",
        SESSION_ID,
        1,
        "找摄像头供应商",
        None,
        "received",
        {},
        None,
        None,
        NOW,
        None,
    )
    cursor = _Cursor([
        (_session_columns(), _session_row()),
        ([("next_turn",)], (1,)),
        (turn_columns, turn_row),
        (_session_columns(), _session_row(version=2)),
    ])
    monkeypatch.setattr(state_store, "get_cursor", lambda: _CursorContext(cursor))

    result = state_store.SessionStateStore().append_turn(
        SESSION_ID,
        expected_version=1,
        user_message="找摄像头供应商",
    )

    assert result is not None
    assert result.turn.turn_number == 1
    assert result.session.version == 2
    assert "FOR UPDATE" in cursor.statements[0][0]
    assert "UPDATE agent_sessions" in cursor.statements[3][0]


def test_create_run_maps_legacy_requirement_column_to_harness_state(monkeypatch) -> None:
    monkeypatch.setattr(
        state_store,
        "insert_run",
        lambda **kwargs: {
            "id": RUN_ID,
            "session_id": kwargs["session_id"],
            "user_id": None,
            "run_type": kwargs["run_type"],
            "status": "CREATED",
            "version": 1,
            "requirement": {"task_type": "sourcing"},
            "created_at": NOW,
            "updated_at": NOW,
            "completed_at": None,
            "error_code": None,
        },
    )

    run = state_store.SessionStateStore().create_run(SESSION_ID, state={"task_type": "sourcing"})

    assert run.run_type == "agent_harness"
    assert run.session_id == UUID(SESSION_ID)
    assert run.state == {"task_type": "sourcing"}
