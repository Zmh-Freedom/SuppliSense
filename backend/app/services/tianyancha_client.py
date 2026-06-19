"""
天眼查 API 客户端。

从 settings 读取鉴权配置：
  TIANYANCHA_BASE_URL  - 默认 https://open.api.tianyancha.com
  TIANYANCHA_TOKEN     - Authorization token

接口列表（路径格式：/services/open/{domain}/{endpoint}/{version}）：
  /services/open/ic/baseinfo/normal       企业基本信息
  /services/open/risk/riskInfo/2.0        天眼风险信息
  /services/open/jr/lawSuit/3.0           法律诉讼
  /services/open/mr/abnormal/2.0           经营异常
  /services/open/mr/punishmentInfo/3.0     行政处罚
  /services/open/mr/illegalinfo/2.0        严重违法
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
    try:
        r = httpx.get(
            f"{BASE_URL}{path}",
            params={"keyword": company_name},
            headers={"Authorization": TOKEN},
            timeout=30.0,
        )
        if not r.is_success:
            return None
        data = r.json()
        code = data.get("error_code", -1)
        if code == 0 or code == 300000:  # 0=success, 300000=no results (valid)
            return data
        return None
    except Exception:
        return None


def _call_with_page(path: str, company_name: str, page_num: int, page_size: int) -> dict | None:
    """翻页调用天眼查 API。"""
    try:
        r = httpx.get(
            f"{BASE_URL}{path}",
            params={"keyword": company_name, "pageNum": page_num, "pageSize": page_size},
            headers={"Authorization": TOKEN},
            timeout=30.0,
        )
        if not r.is_success:
            return None
        data = r.json()
        code = data.get("error_code", -1)
        if code == 0 or code == 300000:
            return data
        return None
    except Exception:
        return None


def _save(collection: str, name: str, data: dict, wrapper_key: str) -> None:
    db = get_db()
    db[collection].update_one(
        {"name": name},
        {"$set": {"name": name, wrapper_key: data}},
        upsert=True,
    )
