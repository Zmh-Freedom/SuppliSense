"""Persistence contract tests for session-scoped entity memory."""

from datetime import datetime, timezone
from app.domains.agent_run import state_store
from app.graphs.agent_core.entity_memory import resolve_turn


SESSION_ID = "00000000-0000-4000-8000-000000000011"
NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _session_columns() -> list[tuple[str]]:
    return [("id",), ("user_id",), ("status",), ("version",), ("state",), ("created_at",), ("updated_at",)]


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


def test_upsert_entity_memory_uses_session_lock_and_version_transition(monkeypatch) -> None:
    session_row = (SESSION_ID, None, "active", 1, {}, NOW, NOW)
    updated_row = (SESSION_ID, None, "active", 2, {}, NOW, NOW)
    cursor = _Cursor([(_session_columns(), session_row), (_session_columns(), updated_row)])
    monkeypatch.setattr(state_store, "get_cursor", lambda: _CursorContext(cursor))
    memory = resolve_turn(
        "对深圳市立创电子有限公司做风险分析",
        session_id=SESSION_ID,
        references=[{"name": "深圳市立创电子有限公司", "supplier_id": "supplier-1"}],
    ).memory

    result = state_store.SessionStateStore().upsert_entity_memory(SESSION_ID, 1, memory)

    assert result is not None
    assert result.version == 2
    assert "FOR UPDATE" in cursor.statements[0][0]
    assert "ON CONFLICT (session_id, entity_key) DO UPDATE" in cursor.statements[1][0]
