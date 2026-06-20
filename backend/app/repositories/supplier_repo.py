"""Supplier repository — MongoDB CRUD for local supplier library."""

import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.mongo import get_db


def add_supplier(data: dict) -> str:
    db = get_db()
    sid = str(uuid.uuid4())
    doc = {
        "_id": sid,
        "name": data["name"],
        "unified_code": data.get("unified_code"),
        "categories": data.get("categories", []),
        "regions": data.get("regions", []),
        "qualifications": data.get("qualifications", []),
        "scale": data.get("scale") or {},
        "contact": data.get("contact") or {},
        "status": data.get("status", "prospective"),
        "rating": data.get("rating"),
        "source": "manual",
        "embedding_dirty": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    db["suppliers"].insert_one(doc)
    return sid


def update_supplier(sid: str, data: dict) -> None:
    db = get_db()
    data["updated_at"] = datetime.now(timezone.utc)
    data["embedding_dirty"] = True
    db["suppliers"].update_one({"_id": sid}, {"$set": data})


def get_supplier(sid: str) -> dict | None:
    db = get_db()
    return db["suppliers"].find_one({"_id": sid})


def get_supplier_by_name(name: str) -> dict | None:
    db = get_db()
    return db["suppliers"].find_one({"name": name})


def list_suppliers(
    keyword: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    db = get_db()
    filt: dict[str, Any] = {}
    if keyword:
        filt["name"] = {"$regex": keyword, "$options": "i"}
    if status:
        filt["status"] = status

    total = db["suppliers"].count_documents(filt)
    cursor = db["suppliers"].find(filt).sort("created_at", -1).skip((page - 1) * page_size).limit(page_size)
    items = list(cursor)
    for item in items:
        item["_id"] = str(item["_id"])

    return {"items": items, "total": total}


def list_embedding_dirty() -> list[dict]:
    db = get_db()
    return list(db["suppliers"].find({"embedding_dirty": True}))


def mark_embedding_clean(sid: str) -> None:
    db = get_db()
    db["suppliers"].update_one({"_id": sid}, {"$set": {"embedding_dirty": False}})


def ensure_indexes() -> None:
    db = get_db()
    db["suppliers"].create_index("unified_code", unique=True, sparse=True)
    db["suppliers"].create_index("categories")
    db["suppliers"].create_index("status")
    db["suppliers"].create_index("name")
