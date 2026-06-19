"""
User repository -- PostgreSQL implementation.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.postgres import get_cursor


def create_user(username: str, email: str, password_hash: str, role: str, user_id: str | None = None) -> dict[str, Any]:
    uid = user_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with get_cursor() as (conn, cur):
        cur.execute(
            """INSERT INTO users (id, username, email, password_hash, role, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING id, username, email, role, is_active, created_at, updated_at""",
            (uid, username, email, password_hash, role, now, now),
        )
        row = cur.fetchone()
        return _row_to_dict(cur, row)


def find_by_username(username: str) -> dict[str, Any] | None:
    with get_cursor() as (conn, cur):
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
        if row:
            return _row_to_dict(cur, row)
    return None


def find_by_email(email: str) -> dict[str, Any] | None:
    with get_cursor() as (conn, cur):
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        if row:
            return _row_to_dict(cur, row)
    return None


def find_by_id(user_id: str) -> dict[str, Any] | None:
    with get_cursor() as (conn, cur):
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if row:
            return _row_to_dict(cur, row)
    return None


def find_all() -> list[dict[str, Any]]:
    with get_cursor() as (conn, cur):
        cur.execute("SELECT * FROM users ORDER BY created_at")
        rows = cur.fetchall()
        return [_row_to_dict(cur, row) for row in rows]


def update_user(user_id: str, **fields) -> dict[str, Any] | None:
    if not fields:
        return find_by_id(user_id)

    set_clauses = []
    values = []
    for key, value in fields.items():
        set_clauses.append(f"{key} = %s")
        values.append(value)

    if "updated_at" not in fields:
        set_clauses.append("updated_at = %s")
        values.append(datetime.now(timezone.utc))

    values.append(user_id)

    with get_cursor() as (conn, cur):
        cur.execute(
            f"UPDATE users SET {', '.join(set_clauses)} WHERE id = %s RETURNING *",
            values,
        )
        row = cur.fetchone()
        if row:
            return _row_to_dict(cur, row)
    return None


def delete_user(user_id: str) -> bool:
    with get_cursor() as (conn, cur):
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        return cur.rowcount > 0


def user_exists(username: str, email: str) -> bool:
    with get_cursor() as (conn, cur):
        cur.execute(
            "SELECT 1 FROM users WHERE username = %s OR email = %s LIMIT 1",
            (username, email),
        )
        return cur.fetchone() is not None


def _row_to_dict(cur, row) -> dict[str, Any]:
    columns = [desc[0] for desc in cur.description]
    result = dict(zip(columns, row))
    if "id" in result and hasattr(result["id"], "hex"):
        result["id"] = str(result["id"])
    return result
