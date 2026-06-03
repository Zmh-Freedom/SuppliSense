import re

import akshare as ak

from app.db.mongo import get_db
from app.repositories.company_repo import get_baseinfo
from app.schemas.financial import FinancialMetrics

# known HK-listed companies (keyword -> HK stock code)
_HK_STOCK_MAP = {
    "腾讯": "00700",
    "阿里巴巴": "09988",
    "美团": "03690",
    "京东": "09618",
    "百度": "09888",
    "快手": "01024",
    "小米": "01810",
    "网易": "09999",
    "比亚迪股份": "01211",
    "联想": "00992",
    "中芯国际": "00981",
    "华润": "00836",
    "海底捞": "06862",
    "安踏": "02020",
    "李宁": "02331",
    "舜宇": "02382",
    "吉利": "00175",
    "蔚来": "09866",
    "小鹏": "09868",
    "理想": "02015",
    "商汤": "00020",
    "哔哩哔哩": "09626",
    "携程": "09961",
    "新东方": "09901",
    "农夫山泉": "09633",
    "中国移动": "00941",
    "中国平安": "02318",
}


def get_financial_metrics(company_name: str) -> FinancialMetrics | None:
    cached = _load_from_cache(company_name)
    if cached is not None:
        return cached

    profile = get_baseinfo(company_name)
    if profile is None:
        return None

    code = _extract_stock_code(company_name)

    # try A-share first, then HK
    metrics = _fetch_a_share(code) if (profile.is_listed and code) else None
    if metrics is None:
        hk_code = _match_hk_code(company_name)
        if hk_code:
            metrics = _fetch_hk(hk_code)

    if metrics is None and not profile.is_listed:
        return None

    if metrics:
        _save_cache(company_name, metrics)
    return metrics


def _fetch_a_share(code: str) -> FinancialMetrics | None:
    try:
        df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
    except Exception:
        return None

    if df is None or df.empty:
        return None

    cols = ["营业总收入同比增长率", "净利润同比增长率", "资产负债率"]
    df = df.dropna(subset=[c for c in cols if c in df.columns])
    if df.empty:
        return None

    latest = df.iloc[-1]
    return FinancialMetrics(
        revenue_growth=_parse_pct(latest.get("营业总收入同比增长率")),
        net_profit_growth=_parse_pct(latest.get("净利润同比增长率")),
        debt_ratio=_parse_pct(latest.get("资产负债率")),
        cash_flow=_parse_float(latest.get("每股经营现金流")),
    )


def _fetch_hk(code: str) -> FinancialMetrics | None:
    growth = None
    indicator = None

    try:
        dfg = ak.stock_hk_growth_comparison_em(symbol=code)
        if dfg is not None and not dfg.empty:
            growth = dfg.iloc[-1]
    except Exception:
        pass

    try:
        dfi = ak.stock_hk_financial_indicator_em(symbol=code)
        if dfi is not None and not dfi.empty:
            indicator = dfi.iloc[-1]
    except Exception:
        pass

    if growth is None and indicator is None:
        return None

    return FinancialMetrics(
        revenue_growth=_parse_pct(growth.get("营业收入同比增长率")) if growth is not None else 0.0,
        net_profit_growth=_parse_pct(growth.get("基本每股收益同比增长率")) if growth is not None else 0.0,
        debt_ratio=0.0,
        cash_flow=_parse_float(indicator.get("每股经营现金流(元)")) if indicator is not None else 0.0,
    )


def _match_hk_code(company_name: str) -> str | None:
    for keyword, code in _HK_STOCK_MAP.items():
        if keyword in company_name:
            return code
    return None


def _extract_stock_code(company_name: str) -> str | None:
    db = get_db()
    doc = db["baseinfo"].find_one({"name": company_name})
    if not doc:
        return None
    items = doc.get("items") or {}
    result = items.get("result") or {}
    code = result.get("bondNum", "")
    if not code:
        return None
    m = re.search(r"\d{6}", str(code))
    return m.group() if m else None


def _parse_pct(val) -> float:
    if val is None or val == "False" or val is False:
        return 0.0
    s = str(val).replace("%", "").replace(",", "")
    try:
        return float(s) / 100
    except (ValueError, TypeError):
        return 0.0


def _parse_float(val) -> float:
    if val is None or val == "False" or val is False:
        return 0.0
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _load_from_cache(name: str) -> FinancialMetrics | None:
    db = get_db()
    doc = db["financial_cache"].find_one({"name": name})
    if doc:
        return FinancialMetrics(**doc["metrics"])
    return None


def _save_cache(name: str, metrics: FinancialMetrics) -> None:
    db = get_db()
    db["financial_cache"].update_one(
        {"name": name},
        {"$set": {"name": name, "metrics": metrics.model_dump()}},
        upsert=True,
    )
