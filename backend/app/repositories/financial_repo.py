import re
from datetime import datetime, timezone

import akshare as ak
import pandas as pd

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
    valid = df.dropna(subset=[c for c in cols if c in df.columns])
    if valid.empty:
        return None

    latest = valid.iloc[-1]

    # trend: compute 3-year slope from annual data
    rev_trend, profit_trend, debt_trend = _compute_trends(df)

    # recurring profit ratio
    recurring_ratio = 0.0
    deducted = _parse_float(latest.get("扣非净利润"))
    net = _parse_float(latest.get("净利润"))
    if net > 0 and deducted != 0:
        recurring_ratio = round(deducted / net, 4)

    return FinancialMetrics(
        revenue_growth=_parse_pct(latest.get("营业总收入同比增长率")),
        net_profit_growth=_parse_pct(latest.get("净利润同比增长率")),
        debt_ratio=_parse_pct(latest.get("资产负债率")),
        cash_flow=_parse_float(latest.get("每股经营现金流")),
        roe=_parse_pct(latest.get("净资产收益率")),
        net_profit_margin=_parse_pct(latest.get("销售净利率")),
        current_ratio=_parse_float(latest.get("流动比率")),
        quick_ratio=_parse_float(latest.get("速动比率")),
        equity_ratio=_parse_float(latest.get("产权比率")),
        inventory_turnover=_parse_float(latest.get("存货周转率")),
        ar_turnover_days=_parse_float(latest.get("应收账款周转天数")),
        recurring_profit_ratio=recurring_ratio,
        revenue_trend=round(rev_trend, 4),
        net_profit_trend=round(profit_trend, 4),
        debt_trend=round(debt_trend, 4),
    )


def _compute_trends(df) -> tuple[float, float, float]:
    """3-year slope from annual reports (取每年年末数据)."""
    try:
        # filter to annual reports only (年底数据)
        df["dt"] = pd.to_datetime(df["报告期"], errors="coerce")
        annual = df[df["dt"].dt.month == 12].tail(5)  # last 5 years
        if len(annual) < 2:
            return 0.0, 0.0, 0.0

        years = (annual["dt"] - annual["dt"].min()).dt.days / 365.0
        rev = pd.to_numeric(annual["营业总收入"], errors="coerce").values
        profit = pd.to_numeric(annual["净利润"], errors="coerce").values
        debt = annual["资产负债率"].apply(_parse_pct).values

        rev_slope = _slope(years.values, rev) / (abs(rev.mean()) + 1) if len(rev) > 1 else 0
        profit_slope = _slope(years.values, profit) / (abs(profit.mean()) + 1) if len(profit) > 1 else 0
        debt_slope = _slope(years.values, debt) if len(debt) > 1 else 0

        return float(rev_slope), float(profit_slope), float(debt_slope)
    except Exception:
        return 0.0, 0.0, 0.0


def _slope(x, y) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    return float((n * (x * y).sum() - x.sum() * y.sum()) / (n * (x * x).sum() - x.sum() ** 2 + 1e-9))


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


_CACHE_TTL_SECONDS = 24 * 3600  # 24 hours


def _load_from_cache(name: str) -> FinancialMetrics | None:
    db = get_db()
    doc = db["financial_cache"].find_one({"name": name})
    if not doc:
        return None
    cached_at = doc.get("cached_at")
    if cached_at:
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age > _CACHE_TTL_SECONDS:
            return None  # expired
    return FinancialMetrics(**doc["metrics"])


def _save_cache(name: str, metrics: FinancialMetrics) -> None:
    db = get_db()
    db["financial_cache"].update_one(
        {"name": name},
        {"$set": {"name": name, "metrics": metrics.model_dump(), "cached_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
