"""Supplier repository — MongoDB CRUD for local supplier library."""

import uuid
import time
from datetime import datetime, timezone
from typing import Any

from app.db.mongo import get_db
from app.schemas.documents import SupplierDocument


def _validate_doc(doc: dict) -> dict:
    """用 Pydantic 校验文档。校验通过返回 dict，失败 raise ValidationError。"""
    return SupplierDocument(**doc).model_dump()


def resolve_supplier_id(name: str, auto_create: bool = False) -> str | None:
    """根据企业名称查找 supplier_id，自动验证名称并补全工商信息。

    统一入口：所有需要引用供应商的地方用此函数。

    1. 先精确查找
    2. auto_create=True 时，尝试天眼查验证名称
    3. 自动拉取工商信息写入 supplier 文档
    """
    db = get_db()
    doc = db["suppliers"].find_one({"name": name}, {"_id": 1})
    if doc:
        return str(doc["_id"])
    if not auto_create:
        return None

    # 尝试从天眼查验证/修正名称
    verified_name = name
    enriched: dict[str, Any] = {}
    base = _fetch_baseinfo(name)
    if not base:
        verified_name, base = _search_tianyancha(name)

    if base:
        verified_name = base.get("name", name)
        enriched = _extract_enrich_fields(base)

    # 创建供应商
    sid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    doc_data = {
        "_id": sid,
        "name": verified_name,
        "status": "prospective",
        "source": "auto",
        "embedding_dirty": True,
        "created_at": now,
        "updated_at": now,
    }
    if enriched:
        doc_data.update(enriched)
    db["suppliers"].insert_one(doc_data)
    _rebuild_vector(sid, doc_data)
    return sid


def _fetch_baseinfo(name: str) -> dict | None:
    """从天眼查拉取 baseinfo，返回聚合并的公司数据 dict。"""
    from app.services.tianyancha_client import fetch_company
    db = get_db()
    base = db["baseinfo"].find_one({"name": name})
    if not base:
        fetch_company(name)
        base = db["baseinfo"].find_one({"name": name})
    return _parse_baseinfo(base)


def _search_tianyancha(name: str) -> tuple[str | None, dict | None]:
    """尝试前缀搜索找到正确的公司名称。返回 (正确名称, 解析后的数据)。"""
    from app.services.tianyancha_client import _call
    import time

    core = name
    for s in ["股份有限公司", "有限公司", "有限责任公司"]:
        core = core.replace(s, "")
    core = core.strip()

    prefixes = [
        "杭州", "深圳", "广州", "上海", "北京", "苏州", "南京", "东莞",
        "武汉", "成都", "重庆", "天津", "西安", "长沙", "青岛", "厦门",
        "宁波", "无锡", "佛山", "合肥", "郑州", "济南", "沈阳", "大连",
        "浙江", "广东", "江苏", "山东", "福建",
    ]
    for prefix in prefixes:
        if core.startswith(prefix):
            continue
        candidate = f"{prefix}{core}有限公司"
        resp = _call("/services/open/ic/baseinfo/normal", candidate)
        if resp and resp.get("error_code") == 0 and resp.get("result"):
            time.sleep(0.3)
            from app.services.tianyancha_client import fetch_company
            fetch_company(candidate)
            base = db["baseinfo"].find_one({"name": candidate})
            parsed = _parse_baseinfo(base)
            if parsed:
                return candidate, parsed
    return None, None


def _parse_baseinfo(base: dict | None) -> dict | None:
    """解析 baseinfo 文档，统一两种格式返回聚合数据。"""
    if not base:
        return None
    items = base.get("items")
    if isinstance(items, dict) and items.get("result"):
        company_data = items["result"]
        if isinstance(company_data, dict):
            if company_data.get("items") and isinstance(company_data["items"], dict):
                return company_data["items"].get("result") or company_data
            return company_data
    if isinstance(base.get("result"), dict):
        return base["result"]
    return None


def _extract_enrich_fields(data: dict) -> dict:
    """从 baseinfo 解析结果中提取工商字段。"""
    enriched: dict[str, Any] = {}
    if data.get("regNumber"):
        enriched["unified_code"] = str(data["regNumber"])
    if data.get("legalPersonName"):
        enriched["legal_person"] = data["legalPersonName"]
    if data.get("regCapital"):
        enriched["registered_capital"] = data["regCapital"]
    if data.get("estiblishTime"):
        enriched["establish_time"] = str(data["estiblishTime"])
    if data.get("regStatus"):
        enriched["reg_status"] = data["regStatus"]
    return enriched


def _rebuild_vector(sid: str, data: dict) -> None:
    """为新创建的供应商构建 PG 向量。"""
    try:
        from app.domains.knowledge.embedding import encode_single
        from app.db.postgres import get_cursor
        parts = [data.get("name", "")]
        parts.extend(data.get("categories", []))
        parts.extend(data.get("regions", []))
        embedding = encode_single(" ".join(p for p in parts if p))
        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
        with get_cursor() as (conn, cur):
            cur.execute(
                """INSERT INTO supplier_profiles (id, supplier_name, content, embedding, metadata)
                   VALUES (%s, %s, %s, %s::vector, %s)
                   ON CONFLICT (id) DO UPDATE SET
                   content = EXCLUDED.content, embedding = EXCLUDED.embedding""",
                (sid, data.get("name", ""), " ".join(parts), vec_str, "{}"),
            )
    except Exception:
        pass  # 向量写入失败不阻塞主流程


def add_supplier(data: dict) -> str:
    validated = _validate_doc(data)
    db = get_db()
    sid = str(uuid.uuid4())
    doc = {
        "_id": sid,
        **validated,
        "source": data.get("source", "manual"),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    db["suppliers"].insert_one(doc)
    return sid


def update_supplier(sid: str, data: dict) -> None:
    validated = _validate_doc({**data, "name": data.get("name", "") or ""})
    db = get_db()
    old = db["suppliers"].find_one({"_id": sid})
    update_data = {**validated, "updated_at": datetime.now(timezone.utc), "embedding_dirty": True}
    # 只更新传入的字段
    update_set = {k: v for k, v in update_data.items() if k in data}
    update_set["updated_at"] = update_data["updated_at"]
    update_set["embedding_dirty"] = True
    db["suppliers"].update_one({"_id": sid}, {"$set": update_set})
    if old:
        changed = {k: {"old": old.get(k), "new": v} for k, v in update_set.items()
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

    items = base.get("items")
    if isinstance(items, dict) and items.get("result"):
        result = items["result"]
    elif isinstance(base.get("result"), dict):
        result = base["result"]
    else:
        return None

    updates: dict[str, Any] = {}
    if result.get("regNumber"):
        updates["unified_code"] = str(result["regNumber"])
    if result.get("legalPersonName"):
        updates["legal_person"] = result["legalPersonName"]
    if result.get("regCapital"):
        updates["registered_capital"] = result["regCapital"]
    if result.get("estiblishTime"):  # API 拼写错误
        updates["establish_time"] = str(result["estiblishTime"])
    if result.get("regStatus"):
        updates["reg_status"] = result["regStatus"]

    if updates:
        updates["updated_at"] = datetime.now(timezone.utc)
        db["suppliers"].update_one({"_id": sid}, {"$set": updates})

    return supplier
