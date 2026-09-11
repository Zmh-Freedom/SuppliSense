"""Supplier repository — MongoDB CRUD for local supplier library."""

import uuid
import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.db.mongo import get_db
from app.core.logging import get_logger
from app.schemas.documents import SupplierDocument

logger = get_logger(__name__)

# GB/T 4754-2017 大类代码 → 品类名称（2位代码覆盖全部 20 门类 97 大类）
_INDUSTRY_CODES: dict[str, str] = {
    # A 农、林、牧、渔业
    "01": "农业", "02": "林业", "03": "畜牧业", "04": "渔业", "05": "农林牧渔专业及辅助性活动",
    # B 采矿业
    "06": "煤炭开采和洗选业", "07": "石油和天然气开采业", "08": "黑色金属矿采选业",
    "09": "有色金属矿采选业", "10": "非金属矿采选业", "11": "开采专业及辅助性活动", "12": "其他采矿业",
    # C 制造业
    "13": "农副食品加工业", "14": "食品制造业", "15": "酒饮料和精制茶制造业",
    "16": "烟草制品业", "17": "纺织业", "18": "纺织服装服饰业",
    "19": "皮革毛皮羽毛及其制品和制鞋业", "20": "木材加工和木竹藤棕草制品业",
    "21": "家具制造业", "22": "造纸和纸制品业", "23": "印刷和记录媒介复制业",
    "24": "文教工美体育和娱乐用品制造业", "25": "石油煤炭及其他燃料加工业",
    "26": "化学原料和化学制品制造业", "27": "医药制造业", "28": "化学纤维制造业",
    "29": "橡胶和塑料制品业", "30": "非金属矿物制品业",
    "31": "黑色金属冶炼和压延加工业", "32": "有色金属冶炼和压延加工业",
    "33": "金属制品业", "34": "通用设备制造业", "35": "专用设备制造业",
    "36": "汽车制造业", "37": "铁路船舶航空航天和其他运输设备制造业",
    "38": "电气机械和器材制造业", "39": "计算机通信和其他电子设备制造业",
    "40": "仪器仪表制造业", "41": "其他制造业", "42": "废弃资源综合利用业",
    "43": "金属制品机械和设备修理业",
    # D 电力、热力、燃气及水生产和供应业
    "44": "电力热力生产和供应业", "45": "燃气生产和供应业", "46": "水的生产和供应业",
    # E 建筑业
    "47": "房屋建筑业", "48": "土木工程建筑业", "49": "建筑安装业", "50": "建筑装饰装修和其他建筑业",
    # F 批发和零售业
    "51": "批发业", "52": "零售业",
    # G 交通运输、仓储和邮政业
    "53": "铁路运输业", "54": "道路运输业", "55": "水上运输业", "56": "航空运输业",
    "57": "管道运输业", "58": "多式联运和运输代理业", "59": "装卸搬运和仓储业", "60": "邮政业",
    # H 住宿和餐饮业
    "61": "住宿业", "62": "餐饮业",
    # I 信息传输、软件和信息技术服务业
    "63": "电信广播电视和卫星传输服务", "64": "互联网和相关服务", "65": "软件和信息技术服务业",
    # J 金融业
    "66": "货币金融服务", "67": "资本市场服务", "68": "保险业", "69": "其他金融业",
    # K 房地产业
    "70": "房地产业",
    # L 租赁和商务服务业
    "71": "租赁业", "72": "商务服务业",
    # M 科学研究和技术服务业
    "73": "研究和试验发展", "74": "专业技术服务业", "75": "科技推广和应用服务业",
    # N 水利、环境和公共设施管理业
    "76": "水利管理业", "77": "生态保护和环境治理业", "78": "公共设施管理业", "79": "土地管理业",
    # O 居民服务、修理和其他服务业
    "80": "居民服务业", "81": "机动车电子产品和日用产品修理业", "82": "其他服务业",
    # P 教育
    "83": "教育",
    # Q 卫生和社会工作
    "84": "卫生", "85": "社会工作",
    # R 文化、体育和娱乐业
    "86": "新闻和出版业", "87": "广播电视电影和录音制作业", "88": "文化艺术业",
    "89": "体育", "90": "娱乐业",
    # S 公共管理、社会保障和社会组织
    "91": "中国共产党机关", "92": "国家机构", "93": "人民政协民主党派",
    "94": "社会保障", "95": "群众团体社会团体和其他成员组织", "96": "基层群众自治组织及其他组织",
    # T 国际组织
    "97": "国际组织",
}

# 采购人员常用叫法与主数据行业/能力字段不是一一对应关系；这里只做
# 可解释的同族匹配，不把任意相近词当成同一品类。
_SOURCING_CATEGORY_FAMILIES: dict[str, tuple[str, ...]] = {
    "钢材": ("钢材", "钢板", "钢卷", "型钢", "不锈钢", "合金钢", "碳钢", "钢管", "钢筋", "线材", "棒材"),
    "工业相机": ("工业相机", "相机", "摄像头", "机器视觉", "视觉模组"),
    "电机": ("电机", "马达", "伺服电机", "步进电机"),
    "电子元器件": ("电子元器件", "电子器件", "芯片", "连接器", "pcb", "电阻", "电容"),
}


def _resolve_category(result: dict) -> str | None:
    """从天眼查 baseinfo result 中提取行业信息，映射为 GB/T 品类名称。"""
    industry_all = result.get("industryAll")
    if isinstance(industry_all, dict):
        code = industry_all.get("categoryCodeThird", "")
        # 精确匹配（3位中类代码）
        name = _INDUSTRY_CODES.get(code)
        if name:
            return name
        # 前缀匹配（2位大类代码，兼容非制造业）
        if len(code) >= 2:
            name = _INDUSTRY_CODES.get(code[:2])
            if name:
                return name
        # 回退到天眼查自带名称
        return industry_all.get("categoryMiddle") or industry_all.get("categoryBig")
    # 回退：直接用 industry 字段
    return result.get("industry")


def enrich_bare_suppliers(limit: int = 100) -> dict:
    """批量补全缺少品类 / 法人等字段的供应商。

    对 source=auto 且无品类的供应商，拉取天眼查 baseinfo
    补全 categories、legal_person、registered_capital 等字段。
    """
    db = get_db()
    from app.services.tianyancha_client import fetch_company

    cursor = db["suppliers"].find(
        {
            "$or": [
                {"categories": {"$exists": False}},
                {"categories": []},
            ],
        },
        {"name": 1},
    ).limit(limit)
    names = [doc["name"] for doc in cursor]

    logger.info("enrich_bare_suppliers_start", count=len(names))
    enriched = 0
    skipped = 0

    for name in names:
        base = db["baseinfo"].find_one({"name": name})
        if not base:
            try:
                fetch_company(name)
                base = db["baseinfo"].find_one({"name": name})
            except Exception:
                pass
        if not base:
            skipped += 1
            continue

        items = base.get("items")
        result = None
        if isinstance(items, dict) and items.get("result"):
            result = items["result"]
        elif isinstance(base.get("result"), dict):
            result = base["result"]
        if not result:
            skipped += 1
            continue

        updates: dict[str, Any] = {}
        category = _resolve_category(result)
        if category:
            updates["categories"] = [category]
        if result.get("regNumber"):
            updates["unified_code"] = str(result["regNumber"])
        if result.get("legalPersonName"):
            updates["legal_person"] = result["legalPersonName"]
        if result.get("regCapital"):
            updates["registered_capital"] = result["regCapital"]
        if result.get("estiblishTime"):
            updates["establish_time"] = str(result["estiblishTime"])
        if result.get("regStatus"):
            updates["reg_status"] = result["regStatus"]

        if updates:
            updates["updated_at"] = datetime.now(timezone.utc)
            db["suppliers"].update_one({"name": name}, {"$set": updates})
            enriched += 1
        else:
            skipped += 1

    logger.info("enrich_bare_suppliers_done", enriched=enriched, skipped=skipped)
    return {"enriched": enriched, "skipped": skipped, "total": len(names)}


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
        "created_at": now,
        "updated_at": now,
    }
    if enriched:
        doc_data.update(enriched)
    db["suppliers"].insert_one(doc_data)
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
    update_data = {**validated, "updated_at": datetime.now(timezone.utc)}
    # 只更新传入的字段
    update_set = {k: v for k, v in update_data.items() if k in data}
    update_set["updated_at"] = update_data["updated_at"]
    db["suppliers"].update_one({"_id": sid}, {"$set": update_set})
    if old:
        changed = {k: {"old": old.get(k), "new": v} for k, v in update_set.items()
                   if k != "updated_at" and old.get(k) != v}
        if changed:
            db["supplier_changelog"].insert_one({
                "supplier_id": sid,
                "supplier_name": old.get("name", ""),
                "changed": changed,
                "changed_at": datetime.now(timezone.utc),
            })


def get_supplier(sid: str) -> dict | None:
    """Read a formal supplier by current view id or stable supplier_id."""
    db = get_db()
    collection = _supplier_read_collection(db)
    current_filter = _current_supplier_filter(collection)
    for query in ({"_id": sid}, {"supplier_id": sid}):
        supplier = collection.find_one({**current_filter, **query})
        if supplier:
            return supplier
    return None


def get_supplier_by_name(name: str) -> dict | None:
    db = get_db()
    # Relationship links must resolve against the same formal read model as
    # the supplier list/profile. Otherwise a stale auto-created legacy record
    # can produce a link that the Feishu snapshot-backed profile cannot open.
    collection = _supplier_read_collection(db)
    return collection.find_one({**_current_supplier_filter(collection), "name": name})


def search_for_sourcing_v2(requirement: dict[str, Any]) -> list[dict]:
    """Read matching active suppliers from the local library without side effects."""
    db = get_db()
    candidates: list[dict] = []
    collection = _supplier_read_collection(db)
    supplier_filter = _current_supplier_filter(collection, {"status": "active"})
    suppliers = list(collection.find(supplier_filter))
    supplier_ids = [
        str(supplier.get("supplier_id") or supplier.get("_id"))
        for supplier in suppliers
        if supplier.get("supplier_id") or supplier.get("_id")
    ]
    capabilities = _load_supplier_snapshots(db, "supplier_capability_snapshots", supplier_ids)
    contacts = _load_supplier_snapshots(db, "supplier_contact_snapshots", supplier_ids)

    for supplier in suppliers:
        supplier_id = str(supplier.get("supplier_id") or supplier.get("_id"))
        candidate = _normalise_sourcing_candidate(
            supplier,
            capabilities=capabilities.get(supplier_id, []),
            contacts=contacts.get(supplier_id, []),
        )
        reasons = _sourcing_match_reasons(candidate, requirement)
        if reasons is not None:
            candidate["match_reasons"] = reasons
            candidates.append(candidate)
    return candidates


def _load_supplier_snapshots(
    db: Any,
    collection_name: str,
    supplier_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Load current child snapshots once and group them by stable supplier ID."""
    if not supplier_ids:
        return {}
    try:
        collection = db[collection_name]
    except (KeyError, TypeError):
        return {}
    records = collection.find({
        "source": "feishu_bitable",
        "sync_status": "current",
        "supplier_id": {"$in": supplier_ids},
    })
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        supplier_id = str(record.get("supplier_id") or "")
        if supplier_id:
            grouped.setdefault(supplier_id, []).append(record)
    return grouped


def _normalise_sourcing_candidate(
    supplier: dict,
    *,
    capabilities: list[dict[str, Any]] | None = None,
    contacts: list[dict[str, Any]] | None = None,
) -> dict:
    """Return the stable, read-only candidate payload used by sourcing risk."""
    capability_items = [_public_capability_snapshot(item) for item in (capabilities or [])]
    contact_items = [_public_contact_snapshot(item) for item in (contacts or [])]
    primary_contact = next(
        (item for item in contact_items if item.get("is_primary_contact")),
        contact_items[0] if contact_items else {},
    )
    categories = _unique_strings([
        *_string_list(supplier.get("categories")),
        *(item.get("category") for item in capability_items),
    ])
    specifications = _unique_strings([
        *_string_list(supplier.get("specifications")),
        *(item.get("product_name") for item in capability_items),
        *(keyword for item in capability_items for keyword in item.get("product_keywords", [])),
    ])
    regions = _unique_strings([
        *_string_list(supplier.get("regions") or supplier.get("region")),
        *(region for item in capability_items for region in item.get("supply_regions", [])),
    ])
    qualifications = _unique_strings([
        *_string_list(supplier.get("qualifications")),
        *(item.get("qualifications") for item in capability_items),
    ])
    return {
        "supplier_id": str(supplier.get("supplier_id") or supplier.get("_id")),
        "supplier_code": supplier.get("supplier_code"),
        "supplier_name": supplier.get("name", ""),
        "short_name": supplier.get("short_name"),
        "industry": supplier.get("industry"),
        "categories": categories,
        "specifications": specifications,
        "regions": regions,
        "status": supplier.get("status"),
        "qualifications": qualifications,
        "capacity": supplier.get("capacity"),
        "updated_at": supplier.get("updated_at"),
        "source_updated_at": supplier.get("source_updated_at") or supplier.get("synced_at"),
        "source": supplier.get("source", "local"),
        "source_stage": "feishu_formal" if supplier.get("source") == "feishu_bitable" else "local_history",
        "source_reference": supplier.get("source_record_id") or supplier.get("record_id") or supplier.get("source_reference"),
        "website_url": supplier.get("website_url"),
        "contact_person": supplier.get("contact_person") or primary_contact.get("contact_name"),
        "contact_phone": supplier.get("contact_phone") or primary_contact.get("phone"),
        "contact_email": supplier.get("contact_email") or primary_contact.get("email"),
        "capabilities": capability_items,
        "contacts": contact_items,
        "evidence": _sourcing_evidence(supplier, capability_items, contact_items),
        "match_reasons": [],
    }


def _sourcing_evidence(
    supplier: dict[str, Any],
    capabilities: list[dict[str, Any]],
    contacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expose source-traceable facts without leaking raw provider payloads."""
    evidence: list[dict[str, Any]] = []
    source = supplier.get("source", "local")
    source_reference = supplier.get("source_record_id") or supplier.get("record_id") or supplier.get("source_reference")
    evidence.append({
        "evidence_id": f"{source}:supplier:{source_reference or supplier.get('supplier_id') or supplier.get('_id')}",
        "dimension": "sourcing",
        "source": source,
        "source_reference": source_reference,
        "observed_at": supplier.get("source_updated_at") or supplier.get("synced_at") or supplier.get("updated_at"),
        "claim": "正式供应商主数据身份与状态",
    })
    for index, capability in enumerate(capabilities):
        evidence.append({
            "evidence_id": f"{source}:capability:{supplier.get('supplier_id') or supplier.get('_id')}:{index}",
            "dimension": "capability",
            "source": source,
            "observed_at": capability.get("source_updated_at"),
            "claim": capability.get("product_name") or capability.get("category") or "供应能力快照",
        })
    for index, contact in enumerate(contacts):
        evidence.append({
            "evidence_id": f"{source}:contact:{supplier.get('supplier_id') or supplier.get('_id')}:{index}",
            "dimension": "contact",
            "source": source,
            "observed_at": contact.get("verified_at"),
            "claim": contact.get("contact_name") or "供应商联系人快照",
        })
    return evidence


def _public_capability_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Expose capability facts to the Agent without leaking raw Feishu fields."""
    return {
        "category": snapshot.get("category"),
        "product_name": snapshot.get("product_name"),
        "product_keywords": _string_list(snapshot.get("product_keywords")),
        "process_capability": snapshot.get("process_capability"),
        "design_development": snapshot.get("design_development"),
        "supply_regions": _string_list(snapshot.get("supply_regions")),
        "production_site": snapshot.get("production_site"),
        "capacity_description": snapshot.get("capacity_description"),
        "qualifications": snapshot.get("qualifications"),
        "capability_status": snapshot.get("capability_status"),
        "source_updated_at": snapshot.get("source_updated_at") or snapshot.get("synced_at"),
    }


def _public_contact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Expose contact facts with the same read-only source timestamp semantics."""
    return {
        "contact_name": snapshot.get("contact_name"),
        "title": snapshot.get("title"),
        "contact_type": snapshot.get("contact_type"),
        "phone": snapshot.get("phone"),
        "email": snapshot.get("email"),
        "is_primary_contact": snapshot.get("is_primary_contact"),
        "is_verified": snapshot.get("is_verified"),
        "verified_at": snapshot.get("verified_at"),
    }


def _sourcing_match_reasons(candidate: dict, requirement: dict[str, Any]) -> list[str] | None:
    """Return matching reasons, or ``None`` when an explicit constraint is absent."""
    constraints = (
        ("category", candidate["categories"]),
        ("specification", candidate["specifications"]),
        ("region", candidate["regions"]),
        ("qualifications", candidate["qualifications"]),
    )
    reasons: list[str] = []
    for field, values in constraints:
        requested = _requirement_values(requirement.get(field))
        if requested and not all(matches_sourcing_value(values, value) for value in requested):
            return None
        reasons.extend(f"{field}:{value}" for value in requested)
    return reasons


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


def _unique_strings(values: list[Any]) -> list[str]:
    """Keep ordered, non-empty string values used for matching and display."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _requirement_values(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]


def matches_sourcing_value(values: list[str], requested: str) -> bool:
    """Match a sourcing constraint using exact text or a known category family."""
    requested_normalized = _normalise_match_text(requested)
    if not requested_normalized:
        return True
    requested_family = _sourcing_family(requested_normalized)
    return any(
        requested_normalized in _normalise_match_text(value)
        or requested_family == _sourcing_family(_normalise_match_text(value))
        for value in values
        if isinstance(value, str)
    )


def _sourcing_family(value: str) -> str:
    for family, aliases in _SOURCING_CATEGORY_FAMILIES.items():
        if any(_normalise_match_text(alias) in value or value in _normalise_match_text(alias) for alias in aliases):
            return family
    return value


def _normalise_match_text(value: str) -> str:
    return "".join(str(value or "").casefold().split())


def list_suppliers(
    keyword: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    hide_bare: bool = True,
    supplier_ids: set[str] | None = None,
) -> dict[str, Any]:
    db = get_db()
    collection = _supplier_read_collection(db)
    filt: dict[str, Any] = _current_supplier_filter(collection)
    if keyword:
        filt["name"] = {"$regex": keyword, "$options": "i"}
    if status:
        filt["status"] = status
    if supplier_ids is not None:
        allowed_ids = list(supplier_ids)
        if not allowed_ids:
            return {"items": [], "total": 0}
        filt["$and"] = [{
            "$or": [
                {"supplier_id": {"$in": allowed_ids}},
                {"_id": {"$in": allowed_ids}},
            ]
        }]
    if hide_bare:
        filt["$or"] = [
            {"source": {"$ne": "auto"}},
            {"categories": {"$exists": True, "$not": {"$size": 0}}},
            {"regions": {"$exists": True, "$not": {"$size": 0}}},
        ]

    total = collection.count_documents(filt)
    sort_field = "synced_at" if collection.name == "supplier_master_snapshots" else "created_at"
    cursor = collection.find(filt).sort(sort_field, -1).skip((page - 1) * page_size).limit(page_size)
    items = list(cursor)
    for item in items:
        item["_id"] = str(item["_id"])
    _enrich_supplier_library_items(db, items)

    return {"items": items, "total": total}


def list_formal_suppliers(limit: int = 20) -> dict[str, Any]:
    """Return the current formal supplier directory from the active read model."""
    db = get_db()
    collection = _supplier_read_collection(db)
    filters: dict[str, Any] = _current_supplier_filter(
        collection, {"status": {"$in": ["active", "approved"]}}
    )

    safe_limit = max(1, min(limit, 50))
    sort_field = "synced_at" if collection.name == "supplier_master_snapshots" else "created_at"
    items = list(collection.find(filters).sort(sort_field, -1).limit(safe_limit))
    for item in items:
        item["_id"] = str(item["_id"])
    _enrich_supplier_library_items(db, items)

    return {
        "total": collection.count_documents(filters),
        "items": [
            {
                "supplier_id": str(item.get("supplier_id") or item.get("_id")),
                "supplier_code": item.get("supplier_code"),
                "supplier_name": item.get("name"),
                "status": item.get("status"),
                "categories": item.get("categories", []),
                "regions": item.get("regions", []),
                "products": item.get("products", []),
                "website_url": item.get("website_url"),
                "source": item.get("source"),
                "source_updated_at": item.get("source_updated_at"),
            }
            for item in items
        ],
    }


def formal_supplier_exists_by_name(name: str) -> bool:
    """Check exact membership in the active formal supplier read model."""
    value = str(name or "").strip()
    if not value:
        return False
    db = get_db()
    collection = _supplier_read_collection(db)
    filters: dict[str, Any] = _current_supplier_filter(collection, {
        "name": {"$regex": f"^{value}$", "$options": "i"},
        "status": {"$in": ["active", "approved"]},
    })
    return collection.count_documents(filters, limit=1) > 0


def _enrich_supplier_library_items(db: Any, items: list[dict[str, Any]]) -> None:
    """Merge current capability snapshots into the supplier-library read model."""
    supplier_ids = [
        str(item.get("supplier_id") or item.get("_id"))
        for item in items
        if item.get("supplier_id") or item.get("_id")
    ]
    capabilities = _load_supplier_snapshots(db, "supplier_capability_snapshots", supplier_ids)

    for item in items:
        supplier_id = str(item.get("supplier_id") or item.get("_id"))
        capability_items = [
            _public_capability_snapshot(snapshot)
            for snapshot in capabilities.get(supplier_id, [])
        ]
        item["categories"] = _unique_strings([
            *_string_list(item.get("categories")),
            *(capability.get("category") for capability in capability_items),
        ])
        item["regions"] = _unique_strings([
            *_string_list(item.get("regions")),
            *(region for capability in capability_items for region in capability.get("supply_regions", [])),
        ])
        item["products"] = _unique_strings([
            *_string_list(item.get("products")),
            *(capability.get("product_name") for capability in capability_items),
            *(keyword for capability in capability_items for keyword in capability.get("product_keywords", [])),
        ])
        item["capabilities"] = capability_items


def _supplier_read_collection(db: Any) -> Any:
    """Return the configured formal supplier read model with safe fallback."""
    if settings.FEISHU_BITABLE_ENABLED:
        try:
            snapshots = db["supplier_master_snapshots"]
        except (KeyError, TypeError):
            snapshots = None
        if snapshots is not None and has_current_feishu_supplier_snapshot(db):
            return snapshots
    return db["suppliers"]


def _current_supplier_filter(collection: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add the active Feishu snapshot boundary to every formal read query."""
    filters = dict(base or {})
    if getattr(collection, "name", "") == "supplier_master_snapshots":
        filters.update({"source": "feishu_bitable", "sync_status": "current"})
    return filters


def has_current_feishu_supplier_snapshot(db: Any | None = None) -> bool:
    """Return whether the formal Feishu master read model has current rows."""
    if not settings.FEISHU_BITABLE_ENABLED:
        return False
    database = db if db is not None else get_db()
    try:
        return database["supplier_master_snapshots"].count_documents(
            {"source": "feishu_bitable", "sync_status": "current"},
            limit=1,
        ) > 0
    except (KeyError, TypeError, AttributeError):
        return False




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
    category = _resolve_category(result)
    if category:
        updates["categories"] = [category]
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
