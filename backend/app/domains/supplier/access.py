"""Supplier ownership and access checks.

Supplier master data lives in MongoDB while user identities live in
PostgreSQL.  This module keeps the cross-store responsibility relationship in
PostgreSQL and exposes framework-free predicates for API and service layers.
"""

from __future__ import annotations

from app.db.postgres import get_cursor


def is_admin(role: str) -> bool:
    return role == "admin"


def list_assigned_supplier_ids(user_id: str) -> set[str]:
    """Return active formal suppliers assigned to one purchaser."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT supplier_id
            FROM supplier_assignments
            WHERE user_id = %s AND is_active = TRUE
            """,
            (user_id,),
        )
        return {str(row[0]) for row in cur.fetchall()}


def can_access_supplier(supplier_id: str, user_id: str, role: str) -> bool:
    """Return whether a user may access a supplier's procurement data."""
    if is_admin(role):
        return True
    return supplier_id in list_assigned_supplier_ids(user_id)


def list_supplier_assignments(supplier_id: str) -> list[dict]:
    """Return active and historical responsibility assignments for admins."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT a.supplier_id, a.user_id::text, u.username, u.email,
                   a.assignment_role, a.is_active, a.assigned_at, a.updated_at
            FROM supplier_assignments a
            JOIN users u ON u.id = a.user_id
            WHERE a.supplier_id = %s
            ORDER BY a.is_active DESC, a.assignment_role, u.username
            """,
            (supplier_id,),
        )
        columns = [desc[0] for desc in cur.description]
        rows = []
        for row in cur.fetchall():
            item = dict(zip(columns, row, strict=True))
            for key in ("assigned_at", "updated_at"):
                if item.get(key) is not None:
                    item[key] = item[key].isoformat()
            rows.append(item)
        return rows


def replace_supplier_assignments(
    supplier_id: str,
    user_ids: list[str],
    assigned_by: str,
) -> list[dict]:
    """Replace active purchasers for a supplier, preserving an audit trail."""
    unique_ids = list(dict.fromkeys(str(user_id).strip() for user_id in user_ids if str(user_id).strip()))
    with get_cursor() as (_, cur):
        if unique_ids:
            cur.execute(
                "SELECT id::text FROM users WHERE id = ANY(%s::uuid[]) AND is_active = TRUE",
                (unique_ids,),
            )
            valid_ids = {str(row[0]) for row in cur.fetchall()}
            missing = sorted(set(unique_ids) - valid_ids)
            if missing:
                raise ValueError("包含不存在或已停用的采购员")

        cur.execute(
            """
            UPDATE supplier_assignments
            SET is_active = FALSE, updated_at = NOW()
            WHERE supplier_id = %s AND is_active = TRUE
            """,
            (supplier_id,),
        )
        for index, user_id in enumerate(unique_ids):
            cur.execute(
                """
                INSERT INTO supplier_assignments
                  (supplier_id, user_id, assignment_role, is_active, assigned_by)
                VALUES (%s, %s::uuid, %s, TRUE, %s::uuid)
                ON CONFLICT (supplier_id, user_id)
                DO UPDATE SET assignment_role = EXCLUDED.assignment_role,
                              is_active = TRUE,
                              assigned_by = EXCLUDED.assigned_by,
                              assigned_at = NOW(),
                              updated_at = NOW()
                """,
                (supplier_id, user_id, "primary" if index == 0 else "backup", assigned_by),
            )

    return list_supplier_assignments(supplier_id)
