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
    "name": ("supplier_name", "company_name", "name", "供应商名称", "公司名称", "orgName"),
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
    "regions": ("regions", "region", "经营地区", "地区"),
    "contact_person": ("contact_person", "contactPerson", "供应商负责人", "联系人"),
    "contact_phone": ("contact_phone", "phone", "telephone", "联系电话", "电话"),
    "contact_email": ("contact_email", "email", "邮箱", "电子邮箱"),
    "website_url": ("website_url", "website", "companyWebsite", "官网", "企业网址"),
    "status": ("supplier_status", "status", "供应商状态", "合作状态"),
    "supplier_level": ("supplier_level", "level", "供应商等级"),
    "is_formal_supplier": ("is_formal_supplier", "isFormalSupplier", "是否正式供应商"),
    "source_updated_at": ("source_updated_at", "updated_at", "更新时间", "数据更新时间"),
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
    if formal is True or value in {"formal", "active", "approved", "正式供应商", "正式"}:
        return "active"
    if value in {"suspended", "暂停", "暂停合作"}:
        return "suspended"
    if value in {"blocked", "blacklisted", "禁止合作", "拉黑"}:
        return "blocked"
    if value in {"exited", "deprecated", "退出", "已退出", "淘汰"}:
        return "deprecated"
    return "prospective"


def normalize_supplier_record(record: dict[str, Any], *, synced_at: datetime | None = None) -> dict[str, Any] | None:
    """Map a Bitable record to the local read model without writing anywhere."""
    fields = record.get("fields")
    if not isinstance(fields, dict):
        return None
    name = _scalar(_field_value(fields, _FIELD_ALIASES["name"]))
    record_id = _scalar(record.get("record_id"))
    if not name or not record_id:
        return None

    updated = synced_at or datetime.now(timezone.utc)
    return {
        "_id": f"feishu:{record_id}",
        "source_record_id": record_id,
        "name": name,
        "unified_code": _scalar(_field_value(fields, _FIELD_ALIASES["unified_code"])),
        "legal_person": _scalar(_field_value(fields, _FIELD_ALIASES["legal_person"])),
        "registered_capital": _scalar(_field_value(fields, _FIELD_ALIASES["registered_capital"])),
        "establish_time": _scalar(_field_value(fields, _FIELD_ALIASES["establish_time"])),
        "reg_status": _scalar(_field_value(fields, _FIELD_ALIASES["reg_status"])),
        "industry": _scalar(_field_value(fields, _FIELD_ALIASES["industry"])),
        "categories": _string_list(_field_value(fields, _FIELD_ALIASES["categories"])),
        "regions": _string_list(_field_value(fields, _FIELD_ALIASES["regions"])),
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
        "source_updated_at": _scalar(_field_value(fields, _FIELD_ALIASES["source_updated_at"])),
        "synced_at": updated,
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
    """Build the legacy-compatible client for the supplier master table."""
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
