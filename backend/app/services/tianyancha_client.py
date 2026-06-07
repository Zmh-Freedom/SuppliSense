"""
天眼查 API 客户端。

从环境变量读取鉴权配置：
  TIANYANCHA_BASE_URL  - 默认 http://open.api.tianyancha.com
  TIANYANCHA_TOKEN     - Authorization token

接口列表（路径格式：/services/open/{domain}/{endpoint}/{version}）：
  /services/open/ic/baseinfo/normal       企业基本信息
  /services/open/risk/riskInfo/2.0        天眼风险信息
  /services/open/jr/lawSuit/3.0           法律诉讼
  /services/open/mr/abnormal/2.0           经营异常
  /services/open/mr/punishmentInfo/3.0     行政处罚
  /services/open/mr/illegalinfo/2.0        严重违法
"""

import os

import httpx

from app.db.mongo import get_db

BASE_URL = os.getenv("TIANYANCHA_BASE_URL", "http://open.api.tianyancha.com")
TOKEN = os.getenv("TIANYANCHA_TOKEN", "")

_ENDPOINTS: list[tuple[str, str, str]] = [
    # (collection_name, path, wrapper_key)
    ("baseinfo", "/services/open/ic/baseinfo/normal", "items"),
    ("riskInfo", "/services/open/risk/riskInfo/2.0", "item"),
    ("lawSuit", "/services/open/jr/lawSuit/3.0", "items"),
    ("abnormal", "/services/open/mr/abnormal/2.0", "items"),
    ("punishmentInfo", "/services/open/mr/punishmentInfo/3.0", "items"),
    ("illegalinfo", "/services/open/mr/illegalinfo/2.0", "items"),
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

    return saved


def _call(path: str, company_name: str) -> dict | None:
    try:
        r = httpx.get(
            f"{BASE_URL}{path}",
            params={"keyword": company_name},
            headers={"Authorization": TOKEN},
            timeout=30.0,
        )
        r.raise_for_status()
        data = r.json()
        code = data.get("error_code", -1)
        if code == 0 or code == 300000:  # 0=success, 300000=no results (valid)
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
