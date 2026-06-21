"""Supplier repository — MongoDB CRUD for local supplier library."""

import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.mongo import get_db


def resolve_supplier_id(name: str, auto_create: bool = False) -> str | None:
    """根据企业名称查找 supplier_id。如果不存在且 auto_create=True 则自动创建。

    统一入口：所有需要引用供应商的地方用此函数获取 supplier_id。
    """
    db = get_db()
    doc = db["suppliers"].find_one({"name": name}, {"_id": 1})
    if doc:
        return str(doc["_id"])
    if auto_create:
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        db["suppliers"].insert_one({
            "_id": sid,
            "name": name,
            "status": "prospective",
            "source": "auto",
            "embedding_dirty": True,
            "created_at": now,
            "updated_at": now,
        })
        return sid
    return None


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
    # 先查旧值用于审计
    old = db["suppliers"].find_one({"_id": sid})
    data["updated_at"] = datetime.now(timezone.utc)
    data["embedding_dirty"] = True
    db["suppliers"].update_one({"_id": sid}, {"$set": data})
    # 记录变更
    if old:
        changed = {k: {"old": old.get(k), "new": v} for k, v in data.items()
                   if k not in ("updated_at", "embedding_dirty") and old.get(k) != v}
        if changed:
            db["supplier_changelog"].insert_one({
                "supplier_id": sid,
                "supplier_name": old.get("name", ""),
                "changed": changed,
                "changed_at": datetime.now(timezone.utc),
            })


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
    db["suppliers"].create_index("name", unique=True)  # 名称唯一，防止重复录入
    db["suppliers"].create_index("supplier_id")
    db["suppliers"].create_index("categories")
    db["suppliers"].create_index("status")
    db["supplier_changelog"].create_index([("supplier_id", 1), ("changed_at", -1)])


def enrich_supplier_from_tianyancha(sid: str) -> dict | None:
    """用天眼查数据补全供应商工商信息。

    从 baseinfo 集合读取注册资本、法人、成立时间等，
    写入 suppliers 文档。失败返回 None。
    """
    db = get_db()
    supplier = db["suppliers"].find_one({"_id": sid})
    if not supplier:
        return None

    name = supplier["name"]
    base = db["baseinfo"].find_one({"name": name})
    if not base:
        # Try fetching from Tianyancha
        from app.services.tianyancha_client import fetch_company
        fetch_company(name)
        base = db["baseinfo"].find_one({"name": name})
    if not base:
        return None

    updates: dict[str, Any] = {}
    if base.get("regNumber"):
        updates["unified_code"] = base["regNumber"]
    if base.get("legalPersonName"):
        updates["legal_person"] = base["legalPersonName"]
    if base.get("regCapital"):
        updates["registered_capital"] = base["regCapital"]
    if base.get("startDate"):
        updates["establish_time"] = base["startDate"]
    if base.get("regStatus"):
        updates["reg_status"] = base["regStatus"]
    if base.get("regInstitute"):
        updates["reg_institute"] = base["regInstitute"]

    if updates:
        updates["updated_at"] = datetime.now(timezone.utc)
        db["suppliers"].update_one({"_id": sid}, {"$set": updates})

    return supplier
