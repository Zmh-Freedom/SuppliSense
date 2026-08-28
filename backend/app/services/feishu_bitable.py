"""Read-only Feishu Bitable adapter for the supplier master.

This module deliberately has no create/update/delete operation. Feishu is the
temporary source of formal supplier master data; SuppliSense stores a local
snapshot for querying and risk analysis.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.db.mongo import get_db

logger = get_logger()


class FeishuBitableError(RuntimeError):
    """Raised when the Feishu read-only integration cannot return data."""


class FeishuBitableClient:
    """Small synchronous client for Feishu tenant token and Bitable records."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        app_token: str,
        table_id: str,
        base_url: str = "https://open.feishu.cn",
        page_size: int = 100,
        timeout_seconds: float = 10,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self.base_url = base_url.rstrip("/")
        self.page_size = max(1, min(page_size, 500))
        self.timeout_seconds = timeout_seconds
        self._tenant_access_token: str | None = None
        self._token_expires_at = 0.0

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout_seconds,
                **kwargs,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FeishuBitableError(f"飞书接口请求失败: {exc}") from exc

        if not isinstance(payload, dict):
            raise FeishuBitableError("飞书接口返回格式无效")
        if payload.get("code", 0) != 0:
            raise FeishuBitableError(
                f"飞书接口返回错误: code={payload.get('code')}, msg={payload.get('msg', '')}"
            )
        return payload

    def get_tenant_access_token(self) -> str:
        """Get and cache the application tenant access token."""
        if self._tenant_access_token and time.time() < self._token_expires_at - 60:
            return self._tenant_access_token
        if not self.app_id or not self.app_secret:
            raise FeishuBitableError("未配置 FEISHU_APP_ID 或 FEISHU_APP_SECRET")

        payload = self._request(
            "POST",
            "/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        token = data.get("tenant_access_token") if isinstance(data, dict) else None
        expire = data.get("expire", 7200) if isinstance(data, dict) else 7200
        if not isinstance(token, str) or not token:
            raise FeishuBitableError("飞书未返回 tenant_access_token")

        self._tenant_access_token = token
        self._token_expires_at = time.time() + int(expire)
        return token

    def list_records(self) -> list[dict[str, Any]]:
        """Read all Bitable records through page-token pagination."""
        if not self.app_token or not self.table_id:
            raise FeishuBitableError(
                "未配置 FEISHU_BITABLE_APP_TOKEN 或供应商表的 table ID"
            )

        token = self.get_tenant_access_token()
        records: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_page_tokens: set[str] = set()

        while True:
            params: dict[str, str | int] = {"page_size": self.page_size}
            if page_token:
                params["page_token"] = page_token
            payload = self._request(
                "GET",
                f"/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            data = payload.get("data")
            if not isinstance(data, dict):
                raise FeishuBitableError("飞书多维表格记录响应缺少 data")

            items = data.get("items", [])
            if not isinstance(items, list):
                raise FeishuBitableError("飞书多维表格记录响应缺少 items")
            records.extend(item for item in items if isinstance(item, dict))

            if not data.get("has_more"):
                break
            next_page_token = data.get("page_token")
            if not isinstance(next_page_token, str) or not next_page_token:
                raise FeishuBitableError("飞书多维表格分页响应缺少 page_token")
            if next_page_token in seen_page_tokens:
                raise FeishuBitableError("飞书多维表格分页 token 重复，已停止读取")
            seen_page_tokens.add(next_page_token)
            page_token = next_page_token

        return records


_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "supplier_code": ("supplier_code", "supplierCode", "供应商代码", "供货商代码"),
    "name": ("supplier_name", "company_name", "name", "供应商名称", "公司名称", "orgName"),
    "short_name": ("short_name", "shortName", "供应商简称"),
    "unified_code": (
        "unified_code", "unified_social_credit_code", "credit_code", "统一社会信用代码",
        "taxCode", "documentNumber",
    ),
    "legal_person": ("legal_person", "legalPerson", "法人", "法定代表人"),
    "registered_capital": ("registered_capital", "registeredCapital", "注册资本"),
    "establish_time": ("establish_time", "establishTime", "成立日期", "成立时间"),
    "reg_status": ("reg_status", "regStatus", "工商状态", "登记状态"),
    "industry": ("industry", "行业", "所属行业"),
    "categories": ("categories", "main_categories", "category", "主营品类", "品类"),
    "major_products": ("major_products", "majorProducts", "主要产品"),
    "business_scope": ("business_scope", "businessScope", "经营范围"),
    "regions": ("regions", "region", "经营地区", "地区"),
    "province": ("province", "注册地址省份", "省份"),
    "city": ("city", "注册地址城市", "市"),
    "address": ("address", "注册地址", "详细地址", "注册地址-详细地址"),
    "contact_person": ("contact_person", "contactPerson", "供应商负责人", "联系人"),
    "contact_phone": ("contact_phone", "phone", "telephone", "联系电话", "电话"),
    "contact_email": ("contact_email", "email", "邮箱", "电子邮箱"),
    "website_url": ("website_url", "website", "companyWebsite", "官网", "企业网址"),
    "status": ("supplier_status", "status", "供应商状态", "合作状态"),
    "supplier_level": ("supplier_level", "level", "供应商等级"),
    "is_formal_supplier": ("is_formal_supplier", "isFormalSupplier", "是否正式供应商"),
    "source_updated_at": ("source_updated_at", "updated_at", "更新时间", "数据更新时间"),
    "category": ("category", "品类"),
    "product_name": ("product_name", "productName", "产品名称"),
    "product_keywords": ("product_keywords", "productKeywords", "产品关键词"),
    "process_capability": ("process_capability", "processCapability", "工艺能力"),
    "design_development": (
        "design_development", "designDevelopment", "是否具备设计开发能力",
        "是否具备设计和开发能力",
    ),
    "supply_regions": ("supply_regions", "supplyRegions", "供货区域"),
    "production_site": ("production_site", "productionSite", "生产地"),
    "capacity_description": ("capacity_description", "capacityDescription", "产能说明"),
    "qualifications": ("qualifications", "qualification", "相关资质概况"),
    "capability_status": ("capability_status", "capabilityStatus", "能力状态"),
    "contact_title": ("contact_title", "title", "职务"),
    "contact_type": ("contact_type", "contactType", "联系人类型"),
    "is_primary_contact": ("is_primary_contact", "isPrimaryContact", "是否主要联系人"),
    "is_verified": ("is_verified", "isVerified", "是否已验证"),
    "verified_at": ("verified_at", "verifiedAt", "验证时间"),
}


def _normalise_key(value: str) -> str:
    return value.strip().replace("_", "").replace("-", "").casefold()


def _field_value(fields: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    indexed = {_normalise_key(str(key)): value for key, value in fields.items()}
    for alias in aliases:
        value = indexed.get(_normalise_key(alias))
        if value is not None and value != "":
            return value
    return None


def _scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if value.get(key) not in (None, ""):
                return str(value[key]).strip()
        return None
    if isinstance(value, list):
        values = [_scalar(item) for item in value]
        values = [item for item in values if item]
        return ", ".join(values) if values else None
    return str(value).strip() or None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        values = [_scalar(item) for item in value]
        return [item for item in values if item]
    scalar = _scalar(value)
    if not scalar:
        return []
    return [item.strip() for item in scalar.replace("，", ",").split(",") if item.strip()]


def _bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    scalar = _scalar(value)
    if scalar is None:
        return None
    if scalar.casefold() in {"true", "1", "yes", "是", "正式供应商", "正式"}:
        return True
    if scalar.casefold() in {"false", "0", "no", "否", "非正式供应商", "潜在"}:
        return False
    return None


def _normalise_status(status: Any, is_formal: Any) -> str:
    formal = _bool_value(is_formal)
    value = (_scalar(status) or "").casefold()
    if formal is True or value in {"formal", "active", "approved", "正式供应商", "正式", "正常"}:
        return "active"
    if value in {"suspended", "暂停", "暂停合作"}:
        return "suspended"
    if value in {"blocked", "blacklisted", "禁止合作", "拉黑"}:
        return "blocked"
    if value in {"exited", "deprecated", "退出", "已退出", "淘汰"}:
        return "deprecated"
    return "prospective"


def normalize_supplier_record(
    record: dict[str, Any], *, synced_at: datetime | None = None, supplier_id: str | None = None
) -> dict[str, Any] | None:
    """Map a Bitable record to the local read model without writing anywhere."""
    fields = record.get("fields")
    if not isinstance(fields, dict):
        return None
    supplier_code = _scalar(_field_value(fields, _FIELD_ALIASES["supplier_code"]))
    name = _scalar(_field_value(fields, _FIELD_ALIASES["name"]))
    record_id = _scalar(record.get("record_id"))
    if not supplier_code or not name or not record_id:
        return None

    updated = synced_at or datetime.now(timezone.utc)
    stable_supplier_id = supplier_id or f"feishu:{record_id}"
    return {
        "_id": stable_supplier_id,
        "supplier_id": stable_supplier_id,
        "supplier_code": supplier_code,
        "source_record_id": record_id,
        "name": name,
        "short_name": _scalar(_field_value(fields, _FIELD_ALIASES["short_name"])),
        "unified_code": _scalar(_field_value(fields, _FIELD_ALIASES["unified_code"])),
        "legal_person": _scalar(_field_value(fields, _FIELD_ALIASES["legal_person"])),
        "registered_capital": _scalar(_field_value(fields, _FIELD_ALIASES["registered_capital"])),
        "establish_time": _scalar(_field_value(fields, _FIELD_ALIASES["establish_time"])),
        "reg_status": _scalar(_field_value(fields, _FIELD_ALIASES["reg_status"])),
        "industry": _scalar(_field_value(fields, _FIELD_ALIASES["industry"])),
        "categories": _string_list(_field_value(fields, _FIELD_ALIASES["categories"])),
        "major_products": _string_list(_field_value(fields, _FIELD_ALIASES["major_products"])),
        "business_scope": _scalar(_field_value(fields, _FIELD_ALIASES["business_scope"])),
        "regions": _string_list(_field_value(fields, _FIELD_ALIASES["regions"])),
        "address": _scalar(_field_value(fields, _FIELD_ALIASES["address"])),
        "contact_person": _scalar(_field_value(fields, _FIELD_ALIASES["contact_person"])),
        "contact_phone": _scalar(_field_value(fields, _FIELD_ALIASES["contact_phone"])),
        "contact_email": _scalar(_field_value(fields, _FIELD_ALIASES["contact_email"])),
        "website_url": _scalar(_field_value(fields, _FIELD_ALIASES["website_url"])),
        "status": _normalise_status(
            _field_value(fields, _FIELD_ALIASES["status"]),
            _field_value(fields, _FIELD_ALIASES["is_formal_supplier"]),
        ),
        "supplier_level": _scalar(_field_value(fields, _FIELD_ALIASES["supplier_level"])),
        "source": "feishu_bitable",
        "source_system": "feishu_bitable",
        "source_updated_at": _scalar(_field_value(fields, _FIELD_ALIASES["source_updated_at"])),
        "synced_at": updated,
        "sync_status": "current",
        "raw_fields": fields,
    }


def _source_record_id(record: dict[str, Any]) -> str | None:
    return _scalar(record.get("record_id"))


def _supplier_code(record: dict[str, Any]) -> str | None:
    fields = record.get("fields")
    if not isinstance(fields, dict):
        return None
    return _scalar(_field_value(fields, _FIELD_ALIASES["supplier_code"]))


def normalize_supplier_capability_record(
    record: dict[str, Any], *, supplier_id: str, supplier_code: str, synced_at: datetime
) -> dict[str, Any] | None:
    """Map one Feishu capability record to the local capability snapshot."""
    fields = record.get("fields")
    record_id = _source_record_id(record)
    if not isinstance(fields, dict) or not record_id:
        return None
    category = _scalar(_field_value(fields, _FIELD_ALIASES["category"]))
    product_name = _scalar(_field_value(fields, _FIELD_ALIASES["product_name"]))
    if not category or not product_name:
        return None
    return {
        "_id": f"feishu:capability:{record_id}",
        "supplier_id": supplier_id,
        "supplier_code": supplier_code,
        "source": "feishu_bitable",
        "source_system": "feishu_bitable",
        "source_record_id": record_id,
        "category": category,
        "product_name": product_name,
        "product_keywords": _string_list(_field_value(fields, _FIELD_ALIASES["product_keywords"])),
        "process_capability": _scalar(_field_value(fields, _FIELD_ALIASES["process_capability"])),
        "design_development": _bool_value(_field_value(fields, _FIELD_ALIASES["design_development"])),
        "supply_regions": _string_list(_field_value(fields, _FIELD_ALIASES["supply_regions"])),
        "production_site": _scalar(_field_value(fields, _FIELD_ALIASES["production_site"])),
        "capacity_description": _scalar(_field_value(fields, _FIELD_ALIASES["capacity_description"])),
        "qualifications": _scalar(_field_value(fields, _FIELD_ALIASES["qualifications"])),
        "capability_status": _scalar(_field_value(fields, _FIELD_ALIASES["capability_status"])) or "待验证",
        "source_updated_at": _scalar(_field_value(fields, _FIELD_ALIASES["source_updated_at"])),
        "synced_at": synced_at,
        "sync_status": "current",
        "raw_fields": fields,
    }


def normalize_supplier_contact_record(
    record: dict[str, Any], *, supplier_id: str, supplier_code: str, synced_at: datetime
) -> dict[str, Any] | None:
    """Map one Feishu contact record to the local contact snapshot."""
    fields = record.get("fields")
    record_id = _source_record_id(record)
    if not isinstance(fields, dict) or not record_id:
        return None
    contact_name = _scalar(_field_value(fields, ("contact_name", "contactName", "联系人姓名", "姓名")))
    if not contact_name:
        return None
    return {
        "_id": f"feishu:contact:{record_id}",
        "supplier_id": supplier_id,
        "supplier_code": supplier_code,
        "source": "feishu_bitable",
        "source_system": "feishu_bitable",
        "source_record_id": record_id,
        "contact_name": contact_name,
        "title": _scalar(_field_value(fields, _FIELD_ALIASES["contact_title"])),
        "contact_type": _scalar(_field_value(fields, _FIELD_ALIASES["contact_type"])) or "其他",
        "phone": _scalar(_field_value(fields, ("phone", "telephone", "电话", "联系电话"))),
        "email": _scalar(_field_value(fields, ("email", "邮箱", "电子邮箱"))),
        "is_primary_contact": _bool_value(_field_value(fields, _FIELD_ALIASES["is_primary_contact"])),
        "is_verified": _bool_value(_field_value(fields, _FIELD_ALIASES["is_verified"])),
        "verified_at": _scalar(_field_value(fields, _FIELD_ALIASES["verified_at"])),
        "synced_at": synced_at,
        "sync_status": "current",
        "raw_fields": fields,
    }


def _build_client(table_id: str) -> FeishuBitableClient:
    return FeishuBitableClient(
        app_id=settings.FEISHU_APP_ID,
        app_secret=settings.FEISHU_APP_SECRET,
        app_token=settings.FEISHU_BITABLE_APP_TOKEN,
        table_id=table_id,
        base_url=settings.FEISHU_BITABLE_BASE_URL,
        page_size=settings.FEISHU_BITABLE_PAGE_SIZE,
        timeout_seconds=settings.FEISHU_BITABLE_TIMEOUT_SECONDS,
    )


def build_client() -> FeishuBitableClient:
    """Build the client for the supplier master table."""
    return _build_client(settings.FEISHU_SUPPLIER_MASTER_TABLE_ID)


def build_supplier_master_client() -> FeishuBitableClient:
    """Build a client configured for the supplier master table."""
    return _build_client(settings.FEISHU_SUPPLIER_MASTER_TABLE_ID)


def build_supplier_capability_client() -> FeishuBitableClient:
    """Build a client configured for the supplier capability table."""
    return _build_client(settings.FEISHU_SUPPLIER_CAPABILITY_TABLE_ID)


def build_supplier_contact_client() -> FeishuBitableClient:
    """Build a client configured for the supplier contact table."""
    return _build_client(settings.FEISHU_SUPPLIER_CONTACT_TABLE_ID)


def build_supplier_transaction_client() -> FeishuBitableClient:
    """Build a read client configured for the supplier transaction snapshot table."""
    return _build_client(settings.FEISHU_BITABLE_TRANSACTION_TABLE_ID)


def sync_supplier_master(client: FeishuBitableClient | None = None) -> dict[str, Any]:
    """Synchronize Feishu supplier records into a local read-only snapshot."""
    if not settings.FEISHU_BITABLE_ENABLED:
        return {"enabled": False, "synced": 0, "skipped": 0, "status": "disabled"}

    client = client or build_client()
    synced_at = datetime.now(timezone.utc)
    batch_id = str(uuid.uuid4())
    records = client.list_records()
    db = get_db()
    collection = db["supplier_master_snapshots"]
    synced = 0
    skipped = 0

    for record in records:
        normalized = normalize_supplier_record(record, synced_at=synced_at)
        if not normalized:
            skipped += 1
            continue
        normalized["sync_batch_id"] = batch_id
        source_record_id = normalized["source_record_id"]
        collection.update_one(
            {"source": "feishu_bitable", "source_record_id": source_record_id},
            {"$set": normalized},
            upsert=True,
        )
        synced += 1

    collection.update_many(
        {"source": "feishu_bitable", "sync_batch_id": {"$ne": batch_id}},
        {"$set": {"sync_status": "stale"}},
    )
    logger.info("feishu_supplier_master_synced", synced=synced, skipped=skipped)
    return {
        "enabled": True,
        "synced": synced,
        "skipped": skipped,
        "status": "ok",
        "synced_at": synced_at.isoformat(),
    }


def _resolve_supplier_id(db: Any, supplier_code: str, source_record_id: str) -> str:
    """Resolve a stable platform ID by supplier code, then by source record ID."""
    collection = db["feishu_supplier_identity_map"]
    query = {"source_system": "feishu_bitable", "supplier_code": supplier_code}
    existing = collection.find_one(query)
    if existing is None:
        existing = collection.find_one({
            "source_system": "feishu_bitable",
            "source_record_id": source_record_id,
        })
    if existing and existing.get("supplier_id"):
        return str(existing["supplier_id"])

    supplier_id = f"supplier:feishu:{uuid.uuid4()}"
    collection.update_one(
        query,
        {
            "$set": {
                "source_system": "feishu_bitable",
                "supplier_code": supplier_code,
                "source_record_id": source_record_id,
                "updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {"supplier_id": supplier_id},
        },
        upsert=True,
    )
    resolved = collection.find_one(query)
    return str(resolved.get("supplier_id", supplier_id)) if resolved else supplier_id


def _upsert_snapshot(collection: Any, document: dict[str, Any], *, query: dict[str, Any]) -> None:
    """Upsert a snapshot without attempting to mutate MongoDB's immutable _id."""
    payload = dict(document)
    document_id = payload.pop("_id", None)
    update: dict[str, Any] = {"$set": payload}
    if document_id is not None:
        update["$setOnInsert"] = {"_id": document_id}
    collection.update_one(query, update, upsert=True)


def _read_supplier_table(client: FeishuBitableClient) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        return client.list_records(), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _validate_table_schema(table_name: str, records: list[dict[str, Any]]) -> str | None:
    """Reject a configured table whose fields do not match the contract."""
    if not records:
        return None
    field_keys = {
        _normalise_key(str(key))
        for record in records
        if isinstance(record, dict) and isinstance(record.get("fields"), dict)
        for key in record["fields"]
    }
    expected_fields = {
        "supplier_master": (
            ("供应商代码", "supplier_code", "supplierCode"),
            ("供应商名称", "supplier_name", "company_name", "name"),
        ),
        "supplier_capability": (
            ("品类", "category"),
            ("产品名称", "product_name", "productName"),
        ),
        "supplier_contact": (("联系人姓名", "contact_name", "contactName", "姓名"),),
    }[table_name]
    missing = [
        aliases[0]
        for aliases in expected_fields
        if not field_keys.intersection({_normalise_key(alias) for alias in aliases})
    ]
    if missing:
        return f"表结构不符合契约，缺少字段: {', '.join(missing)}"
    return None


def sync_supplier_tables() -> dict[str, Any]:
    """Synchronize master, capability, and contact tables into local snapshots."""
    if not settings.FEISHU_BITABLE_ENABLED:
        return {"enabled": False, "synced": 0, "skipped": 0, "status": "disabled"}

    synced_at = datetime.now(timezone.utc)
    batch_id = str(uuid.uuid4())
    table_clients = {
        "supplier_master": build_supplier_master_client(),
        "supplier_capability": build_supplier_capability_client(),
        "supplier_contact": build_supplier_contact_client(),
    }
    table_records: dict[str, list[dict[str, Any]] | None] = {}
    errors: list[dict[str, str]] = []
    table_results: dict[str, dict[str, Any]] = {}

    for table_name, client in table_clients.items():
        records, error = _read_supplier_table(client)
        table_records[table_name] = records
        table_results[table_name] = {
            "status": "ok" if records is not None else "failed",
            "fetched": len(records) if records is not None else 0,
            "synced": 0,
            "skipped": 0,
        }
        if error:
            errors.append({"table": table_name, "reason": error})
        elif records is not None:
            schema_error = _validate_table_schema(table_name, records)
            if schema_error:
                table_results[table_name]["status"] = "invalid_schema"
                errors.append({"table": table_name, "reason": schema_error})

    db = get_db()
    supplier_ids: dict[str, str] = {}
    master_records = table_records["supplier_master"]
    if master_records is not None:
        master_collection = db["supplier_master_snapshots"]
        seen_codes: set[str] = set()
        for record in master_records:
            code = _supplier_code(record)
            record_id = _source_record_id(record)
            if not code or not record_id or code in seen_codes:
                table_results["supplier_master"]["skipped"] += 1
                errors.append({
                    "table": "supplier_master",
                    "source_record_id": record_id or "",
                    "reason": "缺少供应商代码/来源记录 ID或供应商代码重复",
                })
                continue
            seen_codes.add(code)
            supplier_id = _resolve_supplier_id(db, code, record_id)
            normalized = normalize_supplier_record(record, synced_at=synced_at, supplier_id=supplier_id)
            if normalized is None:
                table_results["supplier_master"]["skipped"] += 1
                errors.append({
                    "table": "supplier_master",
                    "source_record_id": record_id,
                    "reason": "缺少供应商代码、供应商名称或字段格式无效",
                })
                continue
            normalized["sync_batch_id"] = batch_id
            _upsert_snapshot(
                master_collection,
                normalized,
                query={"source": "feishu_bitable", "source_record_id": record_id},
            )
            supplier_ids[code] = supplier_id
            table_results["supplier_master"]["synced"] += 1
        master_collection.update_many(
            {"source": "feishu_bitable", "sync_batch_id": {"$ne": batch_id}},
            {"$set": {"sync_status": "stale"}},
        )

    for table_name, collection_name, normalizer in (
        ("supplier_capability", "supplier_capability_snapshots", normalize_supplier_capability_record),
        ("supplier_contact", "supplier_contact_snapshots", normalize_supplier_contact_record),
    ):
        records = table_records[table_name]
        if records is None or master_records is None or table_results[table_name]["status"] != "ok":
            continue
        collection = db[collection_name]
        for record in records:
            code = _supplier_code(record)
            record_id = _source_record_id(record)
            supplier_id = supplier_ids.get(code or "")
            if not code or not record_id or not supplier_id:
                table_results[table_name]["skipped"] += 1
                errors.append({
                    "table": table_name,
                    "source_record_id": record_id or "",
                    "reason": "供应商代码缺失或无法关联主数据",
                })
                continue
            normalized = normalizer(
                record,
                supplier_id=supplier_id,
                supplier_code=code,
                synced_at=synced_at,
            )
            if normalized is None:
                table_results[table_name]["skipped"] += 1
                errors.append({
                    "table": table_name,
                    "source_record_id": record_id,
                    "reason": "必填字段缺失或字段格式无效",
                })
                continue
            normalized["sync_batch_id"] = batch_id
            _upsert_snapshot(
                collection,
                normalized,
                query={"source": "feishu_bitable", "source_record_id": record_id},
            )
            table_results[table_name]["synced"] += 1
        collection.update_many(
            {"source": "feishu_bitable", "sync_batch_id": {"$ne": batch_id}},
            {"$set": {"sync_status": "stale"}},
        )

    failed_tables = [name for name, result in table_results.items() if result["status"] != "ok"]
    status = "ok" if not failed_tables else ("failed" if len(failed_tables) == len(table_results) else "partial_failed")
    logger.info(
        "feishu_supplier_tables_synced",
        status=status,
        batch_id=batch_id,
        master_synced=table_results["supplier_master"]["synced"],
        capability_synced=table_results["supplier_capability"]["synced"],
        contact_synced=table_results["supplier_contact"]["synced"],
        error_count=len(errors),
    )
    return {
        "enabled": True,
        "synced": table_results["supplier_master"]["synced"],
        "skipped": table_results["supplier_master"]["skipped"],
        "status": status,
        "synced_at": synced_at.isoformat(),
        "batch_id": batch_id,
        "tables": table_results,
        "errors": errors,
    }
