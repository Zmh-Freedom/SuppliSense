"""Supplier master data repository.

Imports core CRUD from sourcing/supplier_repo.py and adds profile-specific queries.
"""

from app.db.mongo import get_db
from app.domains.sourcing.supplier_repo import (
    ensure_indexes,
    get_supplier,
    get_supplier_by_name,
    list_suppliers,
    update_supplier,
)


def get_changelog(supplier_id: str, limit: int = 50) -> list[dict]:
    """Get the change history for a supplier."""
    db = get_db()
    docs = list(
        db["supplier_changelog"]
        .find({"supplier_id": supplier_id})
        .sort("changed_at", -1)
        .limit(limit)
    )
    for doc in docs:
        doc["_id"] = str(doc["_id"])
    return docs


def count_changelog(supplier_id: str) -> int:
    """Return the total number of audit entries for a supplier."""
    return get_db()["supplier_changelog"].count_documents({"supplier_id": supplier_id})
