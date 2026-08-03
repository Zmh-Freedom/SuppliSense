"""
Audit log repository -- PostgreSQL.
"""

import uuid
from typing import Any

from psycopg2.extras import Json

from app.db.postgres import PgCursor, get_cursor


def create_log_with_cursor(
    cur: PgCursor,
    action: str,
    user_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    log_id = str(uuid.uuid4())
    cur.execute(
        """INSERT INTO audit_logs
           (id, user_id, action, resource_type, resource_id, details, ip_address, user_agent)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            log_id,
            user_id,
            action,
            resource_type,
            resource_id,
            Json(details) if details is not None else None,
            ip_address,
            user_agent,
        ),
    )
    return log_id


def create_log(
    action: str,
    user_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    with get_cursor() as (conn, cur):
        return create_log_with_cursor(
            cur,
            action,
            user_id,
            resource_type,
            resource_id,
            details,
            ip_address,
            user_agent,
        )


def get_logs(
    user_id: str | None = None,
    action: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses = []
    params: list = []

    if user_id:
        clauses.append("user_id = %s")
        params.append(user_id)
    if action:
        clauses.append("action = %s")
        params.append(action)

    where = " AND ".join(clauses) if clauses else "TRUE"
    params.extend([limit, offset])

    with get_cursor() as (conn, cur):
        cur.execute(
            f"SELECT * FROM audit_logs WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params,
        )
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        return [_row_to_dict(columns, row) for row in rows]


def _row_to_dict(columns, row) -> dict[str, Any]:
    result = dict(zip(columns, row))
    if "id" in result and hasattr(result["id"], "hex"):
        result["id"] = str(result["id"])
    if "user_id" in result and result["user_id"] and hasattr(result["user_id"], "hex"):
        result["user_id"] = str(result["user_id"])
    return result
