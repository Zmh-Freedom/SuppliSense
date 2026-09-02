"""PostgreSQL persistence for resumable legacy chat interrupts."""

from __future__ import annotations

from typing import Any

from psycopg2.extras import Json

from app.db.postgres import get_cursor


def save_chat_interrupt(
    session_id: str,
    config: dict[str, Any],
    mode: str,
    user_message: str,
) -> None:
    """Persist only serializable resume metadata; compiled graphs stay in memory."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            INSERT INTO agent_chat_interrupts (session_id, config, mode, user_message)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET
                config = EXCLUDED.config,
                mode = EXCLUDED.mode,
                user_message = EXCLUDED.user_message,
                updated_at = NOW()
            """,
            (session_id, Json(config), mode, user_message),
        )


def take_chat_interrupt(session_id: str) -> dict[str, Any] | None:
    """Atomically consume one pending interrupt for resume."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            DELETE FROM agent_chat_interrupts
            WHERE session_id = %s
            RETURNING session_id, config, mode, user_message
            """,
            (session_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "session_id": str(row[0]),
        "config": dict(row[1] or {}),
        "mode": str(row[2]),
        "user_message": str(row[3]),
    }


__all__ = ["save_chat_interrupt", "take_chat_interrupt"]
