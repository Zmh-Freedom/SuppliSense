"""PostgreSQL persistence for resumable legacy chat interrupts."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg2.extras import Json

from app.db.postgres import get_cursor


def save_chat_interrupt(
    session_id: str,
    config: dict[str, Any],
    mode: str,
    user_message: str,
    user_id: str | None = None,
) -> None:
    """Persist only serializable resume metadata; compiled graphs are never persisted."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            INSERT INTO agent_chat_interrupts
                (session_id, user_id, config, mode, user_message, status, claim_token, claimed_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', NULL, NULL)
            ON CONFLICT (session_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                config = EXCLUDED.config,
                mode = EXCLUDED.mode,
                user_message = EXCLUDED.user_message,
                status = 'pending',
                claim_token = NULL,
                claimed_at = NULL,
                updated_at = NOW()
            """,
            (session_id, user_id, Json(config), mode, user_message),
        )


def claim_chat_interrupt(
    session_id: str,
    user_id: str | None = None,
    *,
    lease_seconds: int = 300,
) -> dict[str, Any] | None:
    """Claim one interrupt without deleting it until resume has completed."""
    claim_token = str(uuid.uuid4())
    with get_cursor() as (_, cur):
        cur.execute(
            """
            UPDATE agent_chat_interrupts
            SET status = 'claimed', claim_token = %s, claimed_at = NOW(), updated_at = NOW()
            WHERE session_id = %s
              AND (user_id IS NULL OR user_id = %s)
              AND (
                  status = 'pending'
                  OR (status = 'claimed' AND claimed_at < NOW() - (%s * INTERVAL '1 second'))
              )
            RETURNING session_id, user_id, config, mode, user_message, claim_token
            """,
            (claim_token, session_id, user_id, lease_seconds),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "session_id": str(row[0]),
        "user_id": str(row[1]) if row[1] is not None else None,
        "config": dict(row[2] or {}),
        "mode": str(row[3]),
        "user_message": str(row[4]),
        "claim_token": str(row[5]),
    }


def ack_chat_interrupt(session_id: str, claim_token: str) -> bool:
    """Acknowledge and remove only the lease currently being resumed."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            DELETE FROM agent_chat_interrupts
            WHERE session_id = %s AND status = 'claimed' AND claim_token = %s
            RETURNING session_id
            """,
            (session_id, claim_token),
        )
        return cur.fetchone() is not None


def release_chat_interrupt(session_id: str, claim_token: str) -> bool:
    """Release a failed lease so a later resume can retry safely."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            UPDATE agent_chat_interrupts
            SET status = 'pending', claim_token = NULL, claimed_at = NULL, updated_at = NOW()
            WHERE session_id = %s AND status = 'claimed' AND claim_token = %s
            RETURNING session_id
            """,
            (session_id, claim_token),
        )
        return cur.fetchone() is not None


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


def has_chat_interrupt(session_id: str) -> bool:
    """Check durable resume metadata without creating process-local state."""
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT 1 FROM agent_chat_interrupts WHERE session_id = %s LIMIT 1",
            (session_id,),
        )
        return cur.fetchone() is not None


__all__ = [
    "ack_chat_interrupt",
    "claim_chat_interrupt",
    "has_chat_interrupt",
    "release_chat_interrupt",
    "save_chat_interrupt",
    "take_chat_interrupt",
]
