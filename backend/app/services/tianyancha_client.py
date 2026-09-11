"""
天眼查 API 客户端。

从 settings 读取鉴权配置：
  TIANYANCHA_BASE_URL  - 默认 https://open.api.tianyancha.com
  TIANYANCHA_TOKEN     - Authorization token

接口列表（路径格式：/services/open/{domain}/{endpoint}/{version}）：
  /services/open/ic/baseinfo/normal       企业基本信息
  /services/open/risk/riskInfo/2.0        天眼风险信息
  /services/open/jr/lawSuit/3.0           法律诉讼
  /services/open/jr/courtRegister/2.0     立案信息
  /services/open/mr/abnormal/2.0           经营异常
  /services/open/mr/punishmentInfo/3.0     行政处罚
  /services/open/mr/illegalinfo/2.0        严重违法

API 调用计数：
  每次 _call / _call_with_page 都会记录到 api_call_logs 集合。
  可通过 get_api_stats() 查询累计/当日/按接口维度的统计数据。
"""

import httpx
from datetime import datetime, timezone

from app.core.config import settings
from app.db.mongo import get_db

BASE_URL = settings.TIANYANCHA_BASE_URL
TOKEN = settings.TIANYANCHA_TOKEN

_ENDPOINTS: list[tuple[str, str, str]] = [
    # (collection_name, path, wrapper_key)
    # -- 工商信息 (ic) --
    ("baseinfo", "/services/open/ic/baseinfo/normal", "items"),
    ("holder", "/services/open/ic/holder/2.0", "items"),            # 企业股东
    ("invest", "/services/open/ic/invest/2.0", "items"),            # 对外投资
    ("changeInfo", "/services/open/ic/changeInfo/2.0", "items"),    # 变更记录
    ("branch", "/services/open/ic/branch/2.0", "items"),            # 分支机构
    # -- 司法风险 (jr) --
    ("lawSuit", "/services/open/jr/lawSuit/3.0", "items"),          # 法律诉讼
    ("courtRegister", "/services/open/jr/courtRegister/2.0", "items"),  # 立案信息
    ("dishonesty", "/services/open/jr/dishonesty/3.0", "items"),    # 失信被执行人
    ("executedPerson", "/services/open/jr/executedPerson/3.0", "items"),  # 被执行人
    ("courtAnnouncement", "/services/open/jr/courtAnnouncement/3.0", "items"),  # 开庭公告
    ("consumptionRestriction", "/services/open/jr/consumptionRestriction/2.0", "items"),  # 限制消费令
    # -- 经营风险 (mr) --
    ("riskInfo", "/services/open/risk/riskInfo/2.0", "item"),
    ("abnormal", "/services/open/mr/abnormal/2.0", "items"),        # 经营异常
    ("punishmentInfo", "/services/open/mr/punishmentInfo/3.0", "items"),  # 行政处罚
    ("illegalinfo", "/services/open/mr/illegalinfo/2.0", "items"),  # 严重违法
    ("equityPledge", "/services/open/mr/equityPledge/2.0", "items"),  # 股权出质
    ("taxArrears", "/services/open/mr/taxArrears/2.0", "items"),    # 欠税公告
    # -- 知识产权 (ipr) --
    ("trademark", "/services/open/ipr/tm/2.0", "items"),            # 商标
    ("patent", "/services/open/ipr/patent/2.0", "items"),           # 专利
    # -- 新闻 --
    ("news", "/services/open/news/newsList/2.0", "items"),
]


def search_companies(
    keyword: str = "",
    industry: str = "",
    region: str = "",
    page_size: int = 20,
    page_num: int = 1,
) -> dict | None:
    """按行业/地域搜索企业列表。

    使用 tagSearch 接口（免费套餐可用）。
    行业代码为 GB/T 4754-2017 数字编码（如 381=电机制造, 401=电子器件）。
    地区代码为天眼查 areaCode（如 330100=杭州）。

    Returns:
        {"items": [...], "total": N} or None
    """
    if not TOKEN:
        return None
    try:
        params: dict = {
            "tagName": "存续",
            "pageNum": page_num,
            "pageSize": min(page_size, 50),
        }
        if industry:
            params["categoryGuobiao"] = industry
        if region:
            params["areaCode"] = region
        if keyword:
            params["keyword"] = keyword

        r = httpx.get(
            f"{BASE_URL}/services/open/tagSearch",
            params=params,
            headers={"Authorization": TOKEN},
            timeout=30.0,
        )
        _record_call("/services/open/tagSearch", f"{keyword}|{industry}", r.is_success)
        if not r.is_success:
            return None
        data = r.json()
        code = data.get("error_code", -1)
        if code != 0:
            return None
        result = data.get("result") or {}
        items = result.get("items", [])
        total = result.get("total", 0)
        return {"items": items, "total": total}
    except Exception:
        return None


def fetch_news(company_name: str) -> dict | None:
    """拉取企业新闻数据并写入 MongoDB。返回新闻数据或 None。"""
    if not TOKEN:
        return None
    path = "/services/open/news/newsList/2.0"
    resp = _call(path, company_name)
    if resp is not None:
        _save("news", company_name, resp, "items")
    return resp


def fetch_branches(company_name: str) -> dict | None:
    """拉取企业分支机构数据并写入 MongoDB。返回数据或 None。"""
    if not TOKEN:
        return None
    resp = _call("/services/open/ic/branch/2.0", company_name)
    if resp is not None:
        _save("branch", company_name, resp, "items")
    return resp


def ensure_court_register_evidence(company_name: str) -> bool:
    """Ensure a company has cached Tianyancha court-registration evidence.

    Judicial consumers call this before reading local risk data. An existing
    snapshot, including a provider-confirmed no-result response, is reused so
    an interactive query does not repeatedly consume the paid API quota.
    """
    db = get_db()
    if db["courtRegister"].find_one({"name": company_name}):
        return True
    if not TOKEN:
        return False

    response = _call("/services/open/jr/courtRegister/2.0", company_name)
    if response is None:
        return False
    _save("courtRegister", company_name, response, "items")
    return True


def query(endpoint: str, keyword: str) -> dict | None:
    """调用任意天眼查 API 端点。

    Args:
        endpoint: API 路径，如 /services/open/ic/baseinfo/normal
        keyword: 企业名称关键词

    Returns:
        API 原始响应 dict，失败返回 None
    """
    if not TOKEN:
        return None
    return _call(endpoint, keyword)


def get_company_contact(company_name: str) -> dict[str, str]:
    """Read public contact fields from Tianyancha base information without importing a supplier."""
    response = query("/services/open/ic/baseinfo/normal", company_name)
    if not response:
        return {}
    result = response.get("result")
    if not isinstance(result, dict):
        return {}
    return {
        "website_url": _first_string_value(result, "website", "webSite", "websiteUrl", "webUrl"),
        "contact_phone": _first_string_value(result, "phone", "phoneNumber", "tel", "telephone"),
        "contact_email": _first_string_value(result, "email", "emailAddress", "mail"),
    }


def fetch_company(company_name: str) -> bool:
    """拉取企业全部数据并写入 MongoDB。返回 True 表示成功写入至少一条。"""
    if not TOKEN:
        raise RuntimeError("未配置 TIANYANCHA_TOKEN 环境变量")

    saved = False
    for collection, path, wrapper_key in _ENDPOINTS:
        resp = _call(path, company_name)
        if resp is not None:
            _save(collection, company_name, resp, wrapper_key)
            saved = True

    # lawSuit 单独翻页拉取全量（含 judgeTime 用于时间衰减评分）
    _fetch_lawsuit_paginated(company_name)

    return saved


def fetch_collections(company_name: str, collections: list[str] | tuple[str, ...]) -> dict[str, str]:
    """按指定数据域补齐天眼查快照。

    返回每个集合的调用结果：``queried`` 表示接口返回了可落库响应
    （包括供应商明确返回的无结果响应），``query_failed`` 表示接口调用
    失败或没有可落库响应。这样司法工具可以在已有部分缓存时只补缺失域，
    而不会把一次局部命中误当成全部数据已覆盖。
    """
    if not TOKEN:
        return {collection: "not_queried" for collection in collections}

    endpoint_by_collection = {collection: (path, wrapper_key) for collection, path, wrapper_key in _ENDPOINTS}
    outcomes: dict[str, str] = {}
    for collection in dict.fromkeys(collections):
        endpoint = endpoint_by_collection.get(collection)
        if endpoint is None:
            outcomes[collection] = "query_failed"
            continue
        path, wrapper_key = endpoint
        response = _call(path, company_name)
        if response is None:
            outcomes[collection] = "query_failed"
            continue
        _save(collection, company_name, response, wrapper_key)
        outcomes[collection] = "queried"
    return outcomes


def _fetch_lawsuit_paginated(company_name: str) -> None:
    """
    翻页拉取 lawSuit 全量数据，保存到 lawSuit_detail 集合。

    每条记录保留 judgeTime，供评分时近 3 年过滤。翻页合并后存为 flat items 列表。
    """
    path = "/services/open/jr/lawSuit/3.0"
    all_items: list[dict] = []
    page = 1
    page_size = 20

    while True:
        resp = _call_with_page(path, company_name, page, page_size)
        if resp is None:
            break
        result = resp.get("result") or {}
        items = result.get("items", [])
        all_items.extend(items)
        if len(items) < page_size:
            break
        page += 1

    if all_items:
        db = get_db()
        db["lawSuit_detail"].update_one(
            {"name": company_name},
            {"$set": {
                "name": company_name,
                "total": len(all_items),
                "items": all_items,
                "fetched_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )


def _call(path: str, company_name: str) -> dict | None:
    return _call_with_retry(path, company_name)


def _first_string_value(data: dict, *keys: str) -> str:
    """Read the first non-empty string from known fields in a provider payload."""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()
                if isinstance(item, dict):
                    nested = _first_string_value(item, *keys)
                    if nested:
                        return nested
    for value in data.values():
        if isinstance(value, dict):
            nested = _first_string_value(value, *keys)
            if nested:
                return nested
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    nested = _first_string_value(item, *keys)
                    if nested:
                        return nested
    return ""


def _call_with_page(path: str, company_name: str, page_num: int, page_size: int) -> dict | None:
    """翻页调用天眼查 API。"""
    return _call_with_retry(path, company_name, page_num, page_size)


def _call_with_retry(
    path: str, company_name: str,
    page_num: int | None = None, page_size: int | None = None,
    _max_retries: int = 2,
) -> dict | None:
    """调用天眼查 API，带自动重试（指数退避：1s, 2s）。

    重试条件：连接错误、超时、5xx 服务端错误。
    不重试：4xx 客户端错误、error_code 业务错误。
    """
    import time

    last_error: Exception | None = None
    params: dict[str, object] = {"keyword": company_name}
    if page_num is not None:
        params["pageNum"] = page_num
    if page_size is not None:
        params["pageSize"] = page_size

    for attempt in range(_max_retries + 1):
        try:
            r = httpx.get(
                f"{BASE_URL}{path}",
                params=params,  # type: ignore[arg-type]
                headers={"Authorization": TOKEN},
                timeout=30.0,
            )
            if r.is_success:
                data = r.json()
                code = data.get("error_code", -1)
                if code == 0 or code == 300000:
                    _record_call(path, company_name, True)
                    return data
                # 业务错误不重试
                _record_call(path, company_name, False)
                return None

            # 服务端错误可重试
            if r.status_code >= 500 and attempt < _max_retries:
                time.sleep(2**attempt)
                continue

            _record_call(path, company_name, False)
            return None

        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
            last_error = e
            if attempt < _max_retries:
                time.sleep(2**attempt)
                continue
        except Exception:
            _record_call(path, company_name, False)
            return None

    _record_call(path, company_name, False)
    return None


# ---- API call tracking ----

def _record_call(path: str, company: str, success: bool) -> None:
    """记录一次 API 调用到 MongoDB。"""
    from datetime import datetime, timezone
    try:
        db = get_db()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        db["api_call_logs"].insert_one({
            "path": path,
            "company": company,
            "success": success,
            "created_at": datetime.now(timezone.utc),
            "date": today,
        })
    except Exception:
        pass  # logging failure should never break the main flow


def get_api_stats() -> dict:
    """查询天眼查 API 调用统计。"""
    from datetime import datetime, timezone
    db = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    total = db["api_call_logs"].count_documents({})
    today_count = db["api_call_logs"].count_documents({"date": today})
    success_count = db["api_call_logs"].count_documents({"success": True})
    fail_count = total - success_count

    # Per-endpoint breakdown
    pipeline = [
        {"$group": {"_id": "$path", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    by_endpoint = list(db["api_call_logs"].aggregate(pipeline))

    # Per-company breakdown (top 20)
    pipeline2 = [
        {"$group": {"_id": "$company", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 20},
    ]
    by_company = list(db["api_call_logs"].aggregate(pipeline2))

    return {
        "total": total,
        "today": today_count,
        "success": success_count,
        "fail": fail_count,
        "by_endpoint": [{"path": e["_id"], "count": e["count"]} for e in by_endpoint],
        "top_companies": [{"company": c["_id"], "count": c["count"]} for c in by_company],
    }


def _save(collection: str, name: str, data: dict, wrapper_key: str) -> None:
    from app.domains.sourcing.supplier_repo import resolve_supplier_id

    db = get_db()
    sid = resolve_supplier_id(name)
    db[collection].update_one(
        {"name": name},
        {"$set": {"name": name, "supplier_id": sid, wrapper_key: data}},
        upsert=True,
    )
