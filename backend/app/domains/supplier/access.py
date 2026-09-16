"""Formal-supplier access scopes derived from the Feishu responsibility snapshot."""

from __future__ import annotations

from uuid import UUID

from app.db.postgres import get_cursor


def is_admin(role: str) -> bool:
    return role == "admin"


def _normalise_user_uuid(user_id: str) -> str | None:
    """Return a canonical UUID for database lookups, or None for transient IDs."""
    try:
        return str(UUID(str(user_id)))
    except (AttributeError, TypeError, ValueError):
        return None


def _feishu_open_id_for_user(user_id: str) -> str | None:
    normalized_user_id = _normalise_user_uuid(user_id)
    if not normalized_user_id:
        return None
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT feishu_open_id FROM users WHERE id = %s::uuid AND is_active = TRUE",
            (normalized_user_id,),
        )
        row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def feishu_open_id_for_user(user_id: str) -> str | None:
    """Return the verified Feishu identity mapped to an application user."""
    return _feishu_open_id_for_user(user_id)


def user_id_for_feishu_open_id(open_id: str) -> str | None:
    """Resolve an application account from a Feishu person identity."""
    if not open_id:
        return None
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT id FROM users WHERE feishu_open_id = %s AND is_active = TRUE LIMIT 1",
            (open_id,),
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


def formal_supplier_id_by_name(name: str) -> str | None:
    """Resolve only current formal suppliers; external candidates stay outside this gate."""
    from app.domains.sourcing.supplier_repo import normalize_supplier_identity

    normalized_name = normalize_supplier_identity(name)
    if not normalized_name:
        return None
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT supplier_id FROM supplier_responsibility_snapshots
            WHERE source_active = TRUE AND sync_status = 'current'
              AND supplier_name = %s AND supplier_id IS NOT NULL
            LIMIT 1
            """,
            (name.strip(),),
        )
        row = cur.fetchone()
        if row:
            return str(row[0])

        # Match the active Feishu responsibility snapshot with the same
        # punctuation/whitespace normalization used by the Mongo read model.
        cur.execute(
            """
            SELECT supplier_id, supplier_name FROM supplier_responsibility_snapshots
            WHERE source_active = TRUE AND sync_status = 'current'
              AND supplier_id IS NOT NULL AND supplier_name IS NOT NULL
            """,
        )
        for supplier_id, supplier_name in cur.fetchall():
            if normalize_supplier_identity(str(supplier_name)) == normalized_name:
                return str(supplier_id)
    return None


def formal_supplier_exists_by_name(name: str) -> bool:
    """Return whether the active formal-supplier read model contains a name."""
    from app.domains.sourcing.supplier_repo import formal_supplier_exists_by_name as _exists

    return _exists(name)


def can_access_formal_supplier_name(name: str, user_id: str, role: str) -> bool | None:
    """Return None when a name is not a formal supplier, otherwise enforce scope."""
    supplier_id = formal_supplier_id_by_name(name)
    if not supplier_id:
        return None
    return can_access_supplier(supplier_id, user_id, role)


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
