"""PostgreSQL-backed Agent Harness session and execution state store."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from psycopg2.extras import Json

from app.db.postgres import PgCursor, get_cursor
from app.domains.agent_run.harness_contracts import (
    AgentRun,
    AgentSession,
    AgentTurn,
    SessionStatus,
    SessionTurnCommit,
    TurnStatus,
)
from app.domains.agent_run.repo import insert_run

if TYPE_CHECKING:
    from app.graphs.agent_core.entity_memory import EntityMemory


def _row_to_dict(cur: PgCursor, row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(zip((column[0] for column in cur.description), row))
    for key, value in result.items():
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
    return result


def _session_from_row(cur: PgCursor, row: tuple[Any, ...] | None) -> AgentSession | None:
    payload = _row_to_dict(cur, row)
    return AgentSession.model_validate(payload) if payload else None


def _turn_from_row(cur: PgCursor, row: tuple[Any, ...] | None) -> AgentTurn | None:
    payload = _row_to_dict(cur, row)
    return AgentTurn.model_validate(payload) if payload else None


def _run_from_row(cur: PgCursor, row: tuple[Any, ...] | None) -> AgentRun | None:
    payload = _row_to_dict(cur, row)
    if payload is None:
        return None
    payload["state"] = payload.pop("requirement", {})
    return AgentRun.model_validate(payload)


class SessionStateStore:
    """Own durable session state and optimistic version transitions.

    Mongo conversation documents are intentionally not read or written here.
    They remain a compatibility/display store until the chat runtime migrates
    to this control plane.
    """

    def create_session(
        self,
        user_id: str | None = None,
        *,
        state: dict[str, Any] | None = None,
        status: SessionStatus = SessionStatus.ACTIVE,
        session_id: str | None = None,
    ) -> AgentSession:
        session_id = session_id or str(uuid.uuid4())
        with get_cursor() as (_, cur):
            cur.execute(
                """
                INSERT INTO agent_sessions (id, user_id, status, state)
                VALUES (%s, %s, %s, %s)
                RETURNING *
                """,
                (session_id, user_id, status.value, Json(state or {})),
            )
            session = _session_from_row(cur, cur.fetchone())
        if session is None:
            raise RuntimeError("Agent session 创建失败")
        return session

    def get_session(self, session_id: str, user_id: str | None = None) -> AgentSession | None:
        with get_cursor() as (_, cur):
            if user_id is None:
                cur.execute("SELECT * FROM agent_sessions WHERE id = %s", (session_id,))
            else:
                cur.execute(
                    "SELECT * FROM agent_sessions WHERE id = %s AND user_id = %s",
                    (session_id, user_id),
                )
            return _session_from_row(cur, cur.fetchone())

    def update_session_state(
        self,
        session_id: str,
        expected_version: int,
        state: dict[str, Any],
        *,
        status: SessionStatus | None = None,
        user_id: str | None = None,
    ) -> AgentSession | None:
        """Update state only when the caller owns the current version."""
        where = "id = %s AND version = %s"
        params: list[Any] = [Json(state), status.value if status else None, session_id, expected_version]
        if user_id is not None:
            where += " AND user_id = %s"
            params.append(user_id)
        with get_cursor() as (_, cur):
            cur.execute(
                f"""
                UPDATE agent_sessions
                SET state = %s,
                    status = COALESCE(%s, status),
                    version = version + 1,
                    updated_at = NOW()
                WHERE {where}
                RETURNING *
                """,
                params,
            )
            return _session_from_row(cur, cur.fetchone())

    def append_turn(
        self,
        session_id: str,
        expected_version: int,
        user_message: str,
        *,
        request: dict[str, Any] | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
    ) -> SessionTurnCommit | None:
        """Append one turn and advance the session version atomically."""
        with get_cursor() as (_, cur):
            if user_id is None:
                cur.execute("SELECT * FROM agent_sessions WHERE id = %s FOR UPDATE", (session_id,))
            else:
                cur.execute(
                    "SELECT * FROM agent_sessions WHERE id = %s AND user_id = %s FOR UPDATE",
                    (session_id, user_id),
                )
            session = _session_from_row(cur, cur.fetchone())
            if session is None or session.version != expected_version:
                return None

            cur.execute(
                "SELECT COALESCE(MAX(turn_number), 0) + 1 FROM agent_turns WHERE session_id = %s",
                (session_id,),
            )
            turn_number = int(cur.fetchone()[0])
            turn_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO agent_turns
                    (id, session_id, turn_number, user_message, status, request, run_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    turn_id,
                    session_id,
                    turn_number,
                    user_message,
                    TurnStatus.RECEIVED.value,
                    Json(request or {}),
                    run_id,
                ),
            )
            turn = _turn_from_row(cur, cur.fetchone())
            cur.execute(
                """
                UPDATE agent_sessions
                SET version = version + 1, updated_at = NOW()
                WHERE id = %s AND version = %s
                RETURNING *
                """,
                (session_id, expected_version),
            )
            updated_session = _session_from_row(cur, cur.fetchone())
        if turn is None or updated_session is None:
            raise RuntimeError("Agent turn 持久化失败")
        return SessionTurnCommit(session=updated_session, turn=turn)

    def upsert_entity_memory(
        self,
        session_id: str,
        expected_version: int,
        memory: EntityMemory,
        *,
        user_id: str | None = None,
    ) -> AgentSession | None:
        """Persist session-scoped entities and focus with one version transition."""
        if memory.session_id and memory.session_id != session_id:
            raise ValueError("EntityMemory 不属于当前会话")
        with get_cursor() as (_, cur):
            if user_id is None:
                cur.execute("SELECT * FROM agent_sessions WHERE id = %s FOR UPDATE", (session_id,))
            else:
                cur.execute(
                    "SELECT * FROM agent_sessions WHERE id = %s AND user_id = %s FOR UPDATE",
                    (session_id, user_id),
                )
            session = _session_from_row(cur, cur.fetchone())
            if session is None or session.version != expected_version:
                return None

            for entity in memory.entities:
                entity_row_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"supplisense:entity:{entity.entity_id}"))
                focus_rank = (
                    memory.focus_set.entity_ids.index(entity.entity_id) + 1
                    if memory.focus_set and entity.entity_id in memory.focus_set.entity_ids
                    else None
                )
                cur.execute(
                    """
                    INSERT INTO agent_entities
                        (id, session_id, entity_key, entity_type, display_name,
                         canonical_id, status, mention_count, focus_rank, attributes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id, entity_key) DO UPDATE SET
                        entity_type = EXCLUDED.entity_type,
                        display_name = EXCLUDED.display_name,
                        canonical_id = EXCLUDED.canonical_id,
                        status = EXCLUDED.status,
                        mention_count = GREATEST(agent_entities.mention_count, EXCLUDED.mention_count),
                        focus_rank = EXCLUDED.focus_rank,
                        attributes = EXCLUDED.attributes,
                        updated_at = NOW()
                    """,
                    (
                        entity_row_id,
                        session_id,
                        entity.entity_id,
                        entity.entity_type,
                        entity.canonical_name,
                        entity.attributes.get("supplier_id") or entity.attributes.get("company_id") or entity.attributes.get("candidate_id"),
                        entity.identity_status.value,
                        entity.mention_count,
                        focus_rank,
                        Json({"aliases": entity.aliases, "source_refs": entity.source_refs, **entity.attributes}),
                    ),
                )

            state = dict(session.state)
            state["entity_memory"] = memory.model_dump(mode="json")
            state["focus_set"] = memory.focus_set.model_dump(mode="json") if memory.focus_set else None
            cur.execute(
                """
                UPDATE agent_sessions
                SET state = %s, version = version + 1, updated_at = NOW()
                WHERE id = %s AND version = %s
                RETURNING *
                """,
                (Json(state), session_id, expected_version),
            )
            return _session_from_row(cur, cur.fetchone())

    def create_run(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
        state: dict[str, Any] | None = None,
        run_type: str = "agent_harness",
    ) -> AgentRun:
        run = insert_run(
            run_type=run_type,
            requirement=state or {},
            user_id=user_id,
            session_id=session_id,
        )
        run["state"] = run.pop("requirement", {})
        return AgentRun.model_validate(run)

    def get_run(self, run_id: str, user_id: str | None = None) -> AgentRun | None:
        with get_cursor() as (_, cur):
            if user_id is None:
                cur.execute("SELECT * FROM agent_runs WHERE id = %s", (run_id,))
            else:
                cur.execute(
                    "SELECT * FROM agent_runs WHERE id = %s AND user_id = %s",
                    (run_id, user_id),
                )
            return _run_from_row(cur, cur.fetchone())

    def update_run_state(
        self,
        run_id: str,
        expected_version: int,
        state: dict[str, Any],
        *,
        status: str | None = None,
    ) -> AgentRun | None:
        with get_cursor() as (_, cur):
            cur.execute(
                """
                UPDATE agent_runs
                SET requirement = %s,
                    status = COALESCE(%s, status),
                    version = version + 1,
                    updated_at = NOW()
                WHERE id = %s AND version = %s
                RETURNING *
                """,
                (Json(state), status, run_id, expected_version),
            )
            return _run_from_row(cur, cur.fetchone())


session_state_store = SessionStateStore()

__all__ = ["SessionStateStore", "session_state_store"]
