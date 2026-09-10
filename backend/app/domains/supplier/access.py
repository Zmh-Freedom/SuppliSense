"""Formal-supplier access scopes derived from the Feishu responsibility snapshot."""

from __future__ import annotations

from app.db.postgres import get_cursor


def is_admin(role: str) -> bool:
    return role == "admin"


def _feishu_open_id_for_user(user_id: str) -> str | None:
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT feishu_open_id FROM users WHERE id = %s::uuid AND is_active = TRUE",
            (user_id,),
        )
        row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def list_assigned_supplier_ids(user_id: str, role: str = "") -> set[str]:
    """Return formal suppliers visible to one purchaser or department manager."""
    if is_admin(role):
        return set()
    open_id = _feishu_open_id_for_user(str(user_id))
    if not open_id:
        return set()
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT DISTINCT supplier_id
            FROM supplier_responsibility_snapshots
            WHERE source_active = TRUE
              AND sync_status = 'current'
              AND supplier_id IS NOT NULL
              AND (
                    purchaser_open_id = %s
                    OR department_code IN (
                        SELECT DISTINCT department_code
                        FROM supplier_responsibility_snapshots
                        WHERE source_active = TRUE
                          AND sync_status = 'current'
                          AND manager_open_id = %s
                          AND department_code IS NOT NULL
                    )
              )
            """,
            (open_id, open_id),
        )
        return {str(row[0]) for row in cur.fetchall()}


def can_access_supplier(supplier_id: str, user_id: str, role: str) -> bool:
    """Return whether a user may access a formal supplier's business data."""
    return is_admin(role) or supplier_id in list_assigned_supplier_ids(user_id, role)


def list_supplier_assignments(supplier_id: str) -> list[dict]:
    """Return the current read-only Feishu responsibility record for admins."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT supplier_id, supplier_code, supplier_name, department_code,
                   department_name, purchaser_open_id, purchaser_name,
                   manager_open_id, manager_name, effective_date, synced_at
            FROM supplier_responsibility_snapshots
            WHERE supplier_id = %s AND source_active = TRUE AND sync_status = 'current'
            ORDER BY synced_at DESC
            """,
            (supplier_id,),
        )
        columns = [desc[0] for desc in cur.description]
        records = []
        for row in cur.fetchall():
            record = dict(zip(columns, row, strict=True))
            if record.get("synced_at") is not None:
                record["synced_at"] = record["synced_at"].isoformat()
            records.append(record)
        return records


def replace_supplier_assignments(
    supplier_id: str,
    user_ids: list[str],
    assigned_by: str,
) -> list[dict]:
    """Refuse writes to legacy local assignments."""
    del supplier_id, user_ids, assigned_by
    raise ValueError("供应商负责人由飞书责任分配表维护，请在飞书修改后重新同步")
