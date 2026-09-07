"""Read-only Feishu Bitable adapter for the supplier master.

This module deliberately has no create/update/delete operation. Feishu is the
temporary source of formal supplier master data; SuppliSense stores a local
snapshot for querying and risk analysis.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
    "snapshot_id": ("snapshot_id", "snapshotId", "快照ID", "快照Id"),
    "snapshot_month": (
        "snapshot_month", "snapshotMonth", "统计月份", "统计月", "月份", "年月",
    ),
    "snapshot_date": ("snapshot_date", "snapshotDate", "快照日期"),
    "purchasing_org_code": ("purchasing_org_code", "purchasingOrgCode", "采购组织代码"),
    "base": ("base", "基地"),
    "category_code": ("category_code", "categoryCode", "品类代码"),
    "category_name": ("category_name", "categoryName", "品类名称", "品类"),
    "material_code": ("material_code", "materialCode", "物料号", "物料编码"),
    "material_name": ("material_name", "materialName", "物料名称"),
    "unit": ("unit", "计量单位", "单位"),
    "received_qty": ("received_qty", "receivedQty", "收货数量"),
    "received_record_count": (
        "received_record_count", "receivedRecordCount", "收货记录数",
    ),
    "positive_settlement_qty": ("positive_settlement_qty", "positiveSettlementQty", "正结算数量"),
    "negative_settlement_qty": ("negative_settlement_qty", "negativeSettlementQty", "负结算数量"),
    "actual_settlement_qty": ("actual_settlement_qty", "actualSettlementQty", "实结算数量", "实结算"),
    "settled_qty": ("settled_qty", "settledQty", "已结算数量", "已结算"),
    "unsettled_qty": ("unsettled_qty", "unsettledQty", "未结算数量", "未结算"),
    "unit_price": ("unit_price", "unitPrice", "单价"),
    "currency": ("currency", "币种"),
    "amount_basis": ("amount_basis", "amountBasis", "金额口径"),
    "contract_number": ("contract_number", "contractNumber", "合同编号"),
    "contract_status": ("contract_status", "contractStatus", "合同状态"),
    "received_amount": ("received_amount", "receivedAmount", "收货金额"),
    "actual_settlement_amount": ("actual_settlement_amount", "actualSettlementAmount", "实结算金额"),
    "settled_amount": ("settled_amount", "settledAmount", "已结算金额"),
    "unsettled_amount": ("unsettled_amount", "unsettledAmount", "未结算金额"),
    "data_source": ("data_source", "dataSource", "数据来源"),
    "data_mode": ("data_mode", "dataMode", "数据模式"),
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


def _decimal_value(value: Any) -> float | None:
    """Parse Bitable number or text-number fields without guessing malformed values."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    scalar = _scalar(value)
    if not scalar:
        return None
    normalized = scalar.replace(",", "").replace("，", "").replace(" ", "")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", normalized):
        return None
    try:
        return float(Decimal(normalized))
    except InvalidOperation:
        return None


def _normalise_month(value: Any) -> str | None:
    scalar = _scalar(value)
    if not scalar:
        return None
    compact = re.fullmatch(r"(20\d{2})(\d{2})", scalar)
    matched = compact or re.search(r"(20\d{2})[-/.年](\d{1,2})", scalar)
    if not matched:
        return None
    month = int(matched.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{matched.group(1)}-{month:02d}"


def _normalise_contract_status(value: Any) -> str:
    raw = (_scalar(value) or "").casefold()
    if raw in {"履行中", "有效", "有效合同", "active", "valid"}:
        return "active"
    if raw in {"即将到期", "临期", "expiring"}:
        return "expiring"
    if raw in {"已到期", "到期", "expired"}:
        return "expired"
    if raw in {"未签", "未签订", "无合同", "unsigned"}:
        return "unsigned"
    return "unknown"


def _normalise_data_mode(value: Any) -> str:
    raw = (_scalar(value) or "").casefold()
    if raw in {"real", "正式", "真实", "生产"}:
        return "real"
    if raw in {"synthetic", "mock", "test", "测试", "合成"}:
        return "synthetic"
    return "unknown"


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


def _add_reconciliation_issue(
    issues: list[str],
    left: float | None,
    right: float | None,
    label: str,
) -> None:
    if left is not None and right is not None and abs(left - right) > 0.01:
        issues.append(f"{label}不平衡")


def normalize_supplier_transaction_record(
    record: dict[str, Any], *, supplier_id: str, supplier_code: str, synced_at: datetime
) -> dict[str, Any] | None:
    """Map one monthly transaction record to a read-only local snapshot.

    A malformed commercial field does not silently become zero.  The raw row is
    retained for traceability, while validation and reconciliation findings make
    it ineligible for formal P0 assessment until the source is corrected.
    """
    fields = record.get("fields")
    record_id = _source_record_id(record)
    if not isinstance(fields, dict) or not record_id:
        return None

    if _field_value(fields, _FIELD_ALIASES["received_record_count"]) is not None:
        return _normalize_supplier_monthly_summary_record(
            record,
            fields=fields,
            record_id=record_id,
            supplier_id=supplier_id,
            supplier_code=supplier_code,
            synced_at=synced_at,
        )

    snapshot_id = _scalar(_field_value(fields, _FIELD_ALIASES["snapshot_id"]))
    snapshot_month = _normalise_month(_field_value(fields, _FIELD_ALIASES["snapshot_month"]))
    snapshot_date = _scalar(_field_value(fields, _FIELD_ALIASES["snapshot_date"]))
    material_code = _scalar(_field_value(fields, _FIELD_ALIASES["material_code"]))
    purchasing_org_code = _scalar(_field_value(fields, _FIELD_ALIASES["purchasing_org_code"]))
    base = _scalar(_field_value(fields, _FIELD_ALIASES["base"]))
    unit = _scalar(_field_value(fields, _FIELD_ALIASES["unit"]))
    currency = _scalar(_field_value(fields, _FIELD_ALIASES["currency"]))
    amount_basis = _scalar(_field_value(fields, _FIELD_ALIASES["amount_basis"]))
    data_source = _scalar(_field_value(fields, _FIELD_ALIASES["data_source"]))
    data_mode = _normalise_data_mode(_field_value(fields, _FIELD_ALIASES["data_mode"]))
    source_updated_at = _scalar(_field_value(fields, _FIELD_ALIASES["source_updated_at"]))

    required = {
        "快照ID": snapshot_id,
        "统计月份": snapshot_month,
        "快照日期": snapshot_date,
        "采购组织代码": purchasing_org_code,
        "基地": base,
        "物料号": material_code,
        "计量单位": unit,
        "币种": currency,
        "金额口径": amount_basis,
        "数据来源": data_source,
        "数据模式": None if data_mode == "unknown" else data_mode,
        "更新时间": source_updated_at,
    }
    validation_errors = [name for name, value in required.items() if value in (None, "")]

    quantities = {
        "received_qty": _decimal_value(_field_value(fields, _FIELD_ALIASES["received_qty"])),
        "positive_settlement_qty": _decimal_value(_field_value(fields, _FIELD_ALIASES["positive_settlement_qty"])),
        "negative_settlement_qty": _decimal_value(_field_value(fields, _FIELD_ALIASES["negative_settlement_qty"])),
        "actual_settlement_qty": _decimal_value(_field_value(fields, _FIELD_ALIASES["actual_settlement_qty"])),
        "settled_qty": _decimal_value(_field_value(fields, _FIELD_ALIASES["settled_qty"])),
        "unsettled_qty": _decimal_value(_field_value(fields, _FIELD_ALIASES["unsettled_qty"])),
    }
    amounts = {
        "unit_price": _decimal_value(_field_value(fields, _FIELD_ALIASES["unit_price"])),
        "received_amount": _decimal_value(_field_value(fields, _FIELD_ALIASES["received_amount"])),
        "actual_settlement_amount": _decimal_value(_field_value(fields, _FIELD_ALIASES["actual_settlement_amount"])),
        "settled_amount": _decimal_value(_field_value(fields, _FIELD_ALIASES["settled_amount"])),
        "unsettled_amount": _decimal_value(_field_value(fields, _FIELD_ALIASES["unsettled_amount"])),
    }
    for field_name, value in {**quantities, **amounts}.items():
        if value is None:
            validation_errors.append(f"{field_name}格式无效或缺失")

    quality_issues: list[str] = []
    if quantities["negative_settlement_qty"] is not None and quantities["negative_settlement_qty"] > 0:
        quality_issues.append("负结算数量应使用负数")
    if quantities["positive_settlement_qty"] is not None and quantities["negative_settlement_qty"] is not None:
        _add_reconciliation_issue(
            quality_issues,
            quantities["actual_settlement_qty"],
            quantities["positive_settlement_qty"] + quantities["negative_settlement_qty"],
            "实结算数量与正负结算数量",
        )
    _add_reconciliation_issue(
        quality_issues,
        quantities["actual_settlement_qty"],
        (quantities["settled_qty"] + quantities["unsettled_qty"])
        if quantities["settled_qty"] is not None and quantities["unsettled_qty"] is not None else None,
        "实结算数量与已未结算数量",
    )
    _add_reconciliation_issue(
        quality_issues,
        amounts["actual_settlement_amount"],
        (amounts["settled_amount"] + amounts["unsettled_amount"])
        if amounts["settled_amount"] is not None and amounts["unsettled_amount"] is not None else None,
        "实结算金额与已未结算金额",
    )

    return {
        "_id": f"feishu:transaction:{record_id}",
        "supplier_id": supplier_id,
        "supplier_code": supplier_code,
        "supplier_name": _scalar(_field_value(fields, _FIELD_ALIASES["name"])),
        "source": "feishu_bitable",
        "source_system": "feishu_bitable",
        "source_record_id": record_id,
        "snapshot_id": snapshot_id,
        "snapshot_month": snapshot_month,
        "snapshot_date": snapshot_date,
        "purchasing_org_code": purchasing_org_code,
        "base": base,
        "category_code": _scalar(_field_value(fields, _FIELD_ALIASES["category_code"])),
        "category_name": _scalar(_field_value(fields, _FIELD_ALIASES["category_name"])),
        "material_code": material_code,
        "material_name": _scalar(_field_value(fields, _FIELD_ALIASES["material_name"])),
        "unit": unit,
        **quantities,
        **amounts,
        "currency": currency,
        "amount_basis": amount_basis,
        "contract_number": _scalar(_field_value(fields, _FIELD_ALIASES["contract_number"])),
        "contract_status": _normalise_contract_status(
            _field_value(fields, _FIELD_ALIASES["contract_status"])
        ),
        "data_source": data_source,
        "data_mode": data_mode,
        "source_updated_at": source_updated_at,
        "validation_errors": validation_errors,
        "data_quality_issues": quality_issues,
        "data_quality_status": "invalid" if validation_errors else ("warning" if quality_issues else "valid"),
        "eligible_for_formal_assessment": data_mode == "real" and not validation_errors and not quality_issues,
        "synced_at": synced_at,
        "sync_status": "current",
        "raw_fields": fields,
    }


def _normalize_supplier_monthly_summary_record(
    record: dict[str, Any],
    *,
    fields: dict[str, Any],
    record_id: str,
    supplier_id: str,
    supplier_code: str,
    synced_at: datetime,
) -> dict[str, Any]:
    """Map one real supplier-month summary without inventing material-level facts."""
    del record
    supplier_name = _scalar(_field_value(fields, _FIELD_ALIASES["name"]))
    snapshot_month = _normalise_month(
        _field_value(fields, _FIELD_ALIASES["snapshot_month"])
    )
    received_record_count = _decimal_value(
        _field_value(fields, _FIELD_ALIASES["received_record_count"])
    )
    actual_settlement_amount = _decimal_value(
        _field_value(fields, _FIELD_ALIASES["actual_settlement_amount"])
    )
    data_mode = _normalise_data_mode(
        settings.FEISHU_BITABLE_TRANSACTION_DATA_MODE
    )

    validation_errors: list[str] = []
    if not supplier_name:
        validation_errors.append("供应商名称")
    if not snapshot_month:
        validation_errors.append("年月格式无效或缺失")
    if received_record_count is None:
        validation_errors.append("收货记录数格式无效或缺失")
    elif received_record_count < 0 or not received_record_count.is_integer():
        validation_errors.append("收货记录数必须为非负整数")
    if actual_settlement_amount is None:
        validation_errors.append("实结算金额格式无效或缺失")
    if data_mode == "unknown":
        validation_errors.append("交易数据模式未明确配置")

    normalized_count = (
        int(received_record_count) if received_record_count is not None else None
    )
    return {
        "_id": f"feishu:transaction:{record_id}",
        "supplier_id": supplier_id,
        "supplier_code": supplier_code,
        "supplier_name": supplier_name,
        "source": "feishu_bitable",
        "source_system": "feishu_bitable",
        "source_record_id": record_id,
        "snapshot_id": f"{supplier_code}:{snapshot_month}" if snapshot_month else None,
        "snapshot_month": snapshot_month,
        "snapshot_date": None,
        "data_granularity": "supplier_month",
        "received_record_count": normalized_count,
        "actual_settlement_amount": actual_settlement_amount,
        "purchasing_org_code": None,
        "base": None,
        "category_code": None,
        "category_name": None,
        "material_code": None,
        "material_name": None,
        "unit": None,
        "received_qty": None,
        "positive_settlement_qty": None,
        "negative_settlement_qty": None,
        "actual_settlement_qty": None,
        "settled_qty": None,
        "unsettled_qty": None,
        "unit_price": None,
        "received_amount": None,
        "settled_amount": None,
        "unsettled_amount": None,
        "currency": None,
        "amount_basis": "源表实结算金额口径",
        "contract_number": None,
        "contract_status": "unknown",
        "data_source": "feishu_supplier_monthly_summary",
        "data_mode": data_mode,
        "source_updated_at": synced_at.isoformat(),
        "validation_errors": validation_errors,
        "data_quality_issues": [],
        "data_quality_status": "invalid" if validation_errors else "valid",
        "eligible_for_formal_assessment": data_mode == "real" and not validation_errors,
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
        "batch_id": batch_id,
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
    if table_name == "supplier_transaction":
        legacy_contract = (
            ("快照ID", "snapshot_id", "snapshotId"),
            ("统计月份", "snapshot_month", "snapshotMonth"),
            ("供应商代码", "supplier_code", "supplierCode"),
            ("物料号", "material_code", "materialCode"),
        )
        summary_contract = (
            ("供应商代码", "supplier_code", "supplierCode"),
            ("供应商名称", "supplier_name", "company_name", "name"),
            ("年月", "月份", "统计月份", "snapshot_month", "snapshotMonth"),
            ("收货记录数", "received_record_count", "receivedRecordCount"),
            ("实结算金额", "actual_settlement_amount", "actualSettlementAmount"),
        )
        if _schema_matches(field_keys, legacy_contract) or _schema_matches(
            field_keys, summary_contract
        ):
            return None
        return "表结构不符合交易明细或供应商月度汇总契约"

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


def _schema_matches(
    field_keys: set[str], contract: tuple[tuple[str, ...], ...]
) -> bool:
    return all(
        field_keys.intersection({_normalise_key(alias) for alias in aliases})
        for aliases in contract
    )


def _validate_transaction_batch(records: list[dict[str, Any]]) -> str | None:
    """Reject duplicate supplier-month summary rows before they can be double counted."""
    seen_keys: set[tuple[str, str]] = set()
    duplicate_keys: set[tuple[str, str]] = set()
    for record in records:
        fields = record.get("fields")
        if not isinstance(fields, dict):
            continue
        if _field_value(fields, _FIELD_ALIASES["received_record_count"]) is None:
            continue
        supplier_code = _scalar(
            _field_value(fields, _FIELD_ALIASES["supplier_code"])
        )
        snapshot_month = _normalise_month(
            _field_value(fields, _FIELD_ALIASES["snapshot_month"])
        )
        if not supplier_code or not snapshot_month:
            continue
        key = (supplier_code, snapshot_month)
        if key in seen_keys:
            duplicate_keys.add(key)
        seen_keys.add(key)
    if not duplicate_keys:
        return None
    examples = ", ".join(
        f"{supplier_code}/{month}"
        for supplier_code, month in sorted(duplicate_keys)[:5]
    )
    return f"供应商代码与年月重复，已阻止本批次交易数据提交: {examples}"


def _sync_error(
    batch_id: str,
    table_name: str,
    reason: str,
    source_record_id: str | None = None,
) -> dict[str, str]:
    error = {"table": table_name, "batch_id": batch_id, "reason": reason}
    if source_record_id:
        error["source_record_id"] = source_record_id
    return error


def sync_supplier_tables() -> dict[str, Any]:
    """Synchronize configured supplier tables into local read-only snapshots."""
    if not settings.FEISHU_BITABLE_ENABLED:
        return {"enabled": False, "synced": 0, "skipped": 0, "status": "disabled"}

    synced_at = datetime.now(timezone.utc)
    batch_id = str(uuid.uuid4())
    table_clients = {
        "supplier_master": build_supplier_master_client(),
        "supplier_capability": build_supplier_capability_client(),
        "supplier_contact": build_supplier_contact_client(),
    }
    if settings.FEISHU_BITABLE_TRANSACTION_TABLE_ID:
        table_clients["supplier_transaction"] = build_supplier_transaction_client()
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
            "invalid": 0,
        }
        if error:
            errors.append(_sync_error(batch_id, table_name, error))
        elif records is not None:
            schema_error = _validate_table_schema(table_name, records)
            if schema_error:
                table_results[table_name]["status"] = "invalid_schema"
                errors.append(_sync_error(batch_id, table_name, schema_error))
            elif table_name == "supplier_transaction":
                data_error = _validate_transaction_batch(records)
                if data_error:
                    table_results[table_name]["status"] = "invalid_data"
                    errors.append(_sync_error(batch_id, table_name, data_error))

    db = get_db()
    supplier_ids: dict[str, str] = {}
    master_records = table_records["supplier_master"]
    master_is_usable = (
        master_records is not None
        and table_results["supplier_master"]["status"] == "ok"
    )
    if master_is_usable and master_records is not None:
        code_counts: dict[str, int] = {}
        for record in master_records:
            code = _supplier_code(record)
            if code:
                code_counts[code] = code_counts.get(code, 0) + 1
        duplicate_codes = {code for code, count in code_counts.items() if count > 1}
        if duplicate_codes:
            master_is_usable = False
            table_results["supplier_master"]["status"] = "invalid_data"
            errors.append(_sync_error(
                batch_id,
                "supplier_master",
                "供应商代码重复，已阻止本批次主数据提交: " + ", ".join(sorted(duplicate_codes)),
            ))

    if master_is_usable and master_records is not None:
        master_collection = db["supplier_master_snapshots"]
        seen_codes: set[str] = set()
        for record in master_records:
            code = _supplier_code(record)
            record_id = _source_record_id(record)
            if not code or not record_id or code in seen_codes:
                table_results["supplier_master"]["skipped"] += 1
                errors.append(_sync_error(
                    batch_id,
                    "supplier_master",
                    "缺少供应商代码/来源记录 ID或供应商代码重复",
                    record_id,
                ))
                continue
            seen_codes.add(code)
            supplier_id = _resolve_supplier_id(db, code, record_id)
            normalized = normalize_supplier_record(record, synced_at=synced_at, supplier_id=supplier_id)
            if normalized is None:
                table_results["supplier_master"]["skipped"] += 1
                errors.append(_sync_error(
                    batch_id,
                    "supplier_master",
                    "缺少供应商代码、供应商名称或字段格式无效",
                    record_id,
                ))
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
        ("supplier_transaction", "supplier_transaction_snapshots", normalize_supplier_transaction_record),
    ):
        if table_name not in table_records:
            continue
        records = table_records[table_name]
        if (
            records is None
            or not master_is_usable
            or table_results[table_name]["status"] != "ok"
        ):
            continue
        collection = db[collection_name]
        for record in records:
            code = _supplier_code(record)
            record_id = _source_record_id(record)
            supplier_id = supplier_ids.get(code or "")
            if not code or not record_id or not supplier_id:
                table_results[table_name]["skipped"] += 1
                errors.append(_sync_error(
                    batch_id,
                    table_name,
                    "供应商代码缺失或无法关联主数据",
                    record_id,
                ))
                continue
            normalized = normalizer(
                record,
                supplier_id=supplier_id,
                supplier_code=code,
                synced_at=synced_at,
            )
            if normalized is None:
                table_results[table_name]["skipped"] += 1
                errors.append(_sync_error(
                    batch_id,
                    table_name,
                    "必填字段缺失或字段格式无效",
                    record_id,
                ))
                continue
            normalized["sync_batch_id"] = batch_id
            if normalized.get("data_quality_status") == "invalid":
                table_results[table_name]["invalid"] += 1
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
        transaction_synced=table_results.get("supplier_transaction", {}).get("synced", 0),
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
