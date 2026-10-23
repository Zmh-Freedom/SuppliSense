"""Offline transaction tests for consuming a sourcing handoff with a Harness turn."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from app.domains.agent_run import state_store as state_store_module
from app.domains.agent_run.state_store import SessionStateStore

USER_ID = "00000000-0000-0000-0000-000000000011"
SESSION_ID = "00000000-0000-4000-8000-000000000006"
HANDOFF_ID = "00000000-0000-4000-8000-000000000007"
SOURCE_RUN_ID = "00000000-0000-4000-8000-000000000001"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000002"


class FakeCursor:
    def __init__(self, status: str, user_message: str):
        self.description = []
        self.one = None
        self.rowcount = 0
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.status = status
        self.user_message = user_message
        self.now = datetime.now(timezone.utc)
        self.turn_id = ""
        self.run_id = ""
        self.message_hash = hashlib.sha256(user_message.encode("utf-8")).hexdigest()
        self.handoff_row = (
            HANDOFF_ID, USER_ID, SESSION_ID, SOURCE_RUN_ID, 7, CANDIDATE_ID,
            status, "00000000-0000-4000-8000-000000000008" if status == "consumed" else None,
            self.message_hash if status == "consumed" else None,
            "00000000-0000-4000-8000-000000000009" if status == "consumed" else None,
        )

    def _set(self, names: tuple[str, ...], row: tuple[Any, ...] | None, rowcount: int = 1) -> None:
        self.description = [(name,) for name in names]
        self.one = row
        self.rowcount = rowcount

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        values = tuple(params or ())
        self.statements.append((query, values))
        normalized = query.strip()
        self._set((), None, 0)
        if normalized.startswith("SELECT * FROM agent_sessions"):
            self._set(
                ("id", "user_id", "status", "version", "state", "created_at", "updated_at"),
                (SESSION_ID, USER_ID, "active", 1, {}, self.now, self.now),
            )
        elif "FROM sourcing_handoffs" in query and "FOR UPDATE" in query:
            self._set((
                "id", "actor_id", "session_id", "source_run_id", "source_run_version",
                "candidate_id", "status", "consumed_turn_id", "consumed_message_hash", "run_id",
            ), self.handoff_row)
        elif normalized.startswith("SELECT * FROM agent_turns"):
            self._set(
                ("id", "session_id", "turn_number", "user_message", "status", "request", "run_id", "created_at"),
                ("00000000-0000-4000-8000-000000000008", SESSION_ID, 1, self.user_message, "running", {},
                 "00000000-0000-4000-8000-000000000009", self.now),
            )
        elif normalized.startswith("SELECT * FROM agent_runs"):
            self._set(
                ("id", "session_id", "run_type", "user_id", "status", "version", "requirement"),
                ("00000000-0000-4000-8000-000000000009", SESSION_ID, "agent_harness", USER_ID, "RUNNING", 1, {}),
            )
        elif "SELECT id FROM agent_runs" in query and "run_type='sourcing_risk_v2'" in query:
            self._set(("id",), (SOURCE_RUN_ID,))
        elif normalized.startswith("SELECT id FROM agent_run_candidates"):
            self._set(("id",), (CANDIDATE_ID,))
        elif normalized.startswith("SELECT id FROM agent_runs WHERE session_id"):
            self._set(("id",), None, 0)
        elif normalized.startswith("SELECT COALESCE(MAX(turn_number)"):
            self._set(("coalesce",), (1,))
        elif normalized.startswith("INSERT INTO agent_turns"):
            self.turn_id = str(values[0])
            request = getattr(values[-1], "adapted", {})
            self._set(
                ("id", "session_id", "turn_number", "user_message", "status", "request", "run_id", "created_at"),
                (self.turn_id, values[1], values[2], values[3], values[4], request, None, self.now),
            )
        elif normalized.startswith("INSERT INTO agent_runs"):
            self.run_id = str(values[0])
            requirement = getattr(values[5], "adapted", {})
            self._set(
                ("id", "session_id", "run_type", "user_id", "status", "version", "requirement"),
                (self.run_id, values[1], values[2], values[3], values[4], 1, requirement),
            )
        elif normalized.startswith("UPDATE agent_turns SET run_id"):
            self._set(
                ("id", "session_id", "turn_number", "user_message", "status", "request", "run_id", "created_at"),
                (values[1], SESSION_ID, 1, self.user_message, "running", {}, values[0], self.now),
            )
        elif normalized.startswith("UPDATE sourcing_handoffs"):
            self.rowcount = 1
            self.turn_id, self.message_hash, self.run_id = str(values[0]), str(values[1]), str(values[2])
        elif normalized.startswith("UPDATE agent_sessions"):
            session_state = getattr(values[0], "adapted", {})
            self._set(
                ("id", "user_id", "status", "version", "state", "created_at", "updated_at"),
                (SESSION_ID, USER_ID, "active", 2, session_state, self.now, self.now),
            )

    def fetchone(self):
        return self.one


def _install_cursor(monkeypatch: pytest.MonkeyPatch, cursor: FakeCursor) -> None:
    @contextmanager
    def fake_get_cursor():
        yield object(), cursor

    monkeypatch.setattr(state_store_module, "get_cursor", fake_get_cursor)
    monkeypatch.setattr(state_store_module.settings, "AGENT_EXECUTION_MODE", "inline")


def test_start_harness_turn_consumes_handoff_in_same_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    message = "请核验候选工厂的主体并分析风险"
    cursor = FakeCursor("ready", message)
    _install_cursor(monkeypatch, cursor)

    result = SessionStateStore().start_harness_turn(
        SESSION_ID,
        USER_ID,
        message,
        request={"mode": "auto"},
        state={"execution_context": {"sourcing_handoff": {"handoff_id": HANDOFF_ID}}},
        handoff_id=HANDOFF_ID,
    )

    queries = [query.strip() for query, _values in cursor.statements]
    consume_index = next(i for i, query in enumerate(queries) if query.startswith("UPDATE sourcing_handoffs"))
    run_insert_index = next(i for i, query in enumerate(queries) if query.startswith("INSERT INTO agent_runs"))
    session_update_index = next(i for i, query in enumerate(queries) if query.startswith("UPDATE agent_sessions"))
    consume_params = cursor.statements[consume_index][1]
    assert result["idempotent_replay"] is False
    assert str(result["turn"].id) == consume_params[0]
    assert str(result["run"].id) == consume_params[2]
    assert consume_params[1] == hashlib.sha256(message.encode("utf-8")).hexdigest()
    assert run_insert_index < consume_index < session_update_index


def test_start_harness_turn_replays_same_message_without_creating_another_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = "请核验候选工厂的主体并分析风险"
    cursor = FakeCursor("consumed", message)
    _install_cursor(monkeypatch, cursor)

    result = SessionStateStore().start_harness_turn(
        SESSION_ID, USER_ID, message, handoff_id=HANDOFF_ID,
    )

    assert result["idempotent_replay"] is True
    assert str(result["turn"].id) == "00000000-0000-4000-8000-000000000008"
    assert str(result["run"].id) == "00000000-0000-4000-8000-000000000009"
    assert not any(query.strip().startswith("INSERT INTO agent_turns") for query, _ in cursor.statements)
