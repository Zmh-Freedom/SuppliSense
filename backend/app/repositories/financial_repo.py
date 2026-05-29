import re

import akshare as ak

from app.db.mongo import get_db
from app.repositories.company_repo import get_baseinfo
from app.schemas.financial import FinancialMetrics


def get_financial_metrics(company_name: str) -> FinancialMetrics | None:
    # try MongoDB cache first
    cached = _load_from_cache(company_name)
    if cached is not None:
        return cached

    profile = get_baseinfo(company_name)
    if profile is None or not profile.is_listed:
        return None

    code = _extract_stock_code(company_name)
    if code is None:
        return None

    try:
        df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
    except Exception:
        return None

    if df is None or df.empty:
        return None

    drop_cols = ["营业总收入同比增长率", "净利润同比增长率", "资产负债率"]
    df = df.dropna(subset=[c for c in drop_cols if c in df.columns])
    if df.empty:
        return None

    latest = df.iloc[-1]

    metrics = FinancialMetrics(
        revenue_growth=_parse_pct(latest.get("营业总收入同比增长率")),
        net_profit_growth=_parse_pct(latest.get("净利润同比增长率")),
        debt_ratio=_parse_pct(latest.get("资产负债率")),
        cash_flow=_parse_float(latest.get("每股经营现金流")),
    )

    _save_cache(company_name, metrics)
    return metrics


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
