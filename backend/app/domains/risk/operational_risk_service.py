"""Quality and delivery risk from validated internal transaction snapshots.

The current Feishu transaction contract does not require quality or delivery
fields.  The service therefore supports the fields when present, but returns
an explicit data gap otherwise.  It never maps a missing metric to zero risk.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from app.db.mongo import get_db


OperationalDimension = Literal["quality", "delivery"]

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "quality_rate": ("quality_rate", "quality_pass_rate", "pass_rate", "合格率", "质量合格率"),
    "defect_rate": ("defect_rate", "不良率", "缺陷率"),
    "rejected_qty": ("rejected_qty", "rejected_quantity", "不合格数量"),
    "received_qty": ("received_qty", "received_quantity", "收货数量"),
    "on_time_rate": ("on_time_rate", "on_time_delivery_rate", "准时交付率", "按时交付率"),
    "delayed_qty": ("delayed_qty", "late_delivery_qty", "延期数量"),
    "ordered_qty": ("ordered_qty", "order_quantity", "订货数量"),
    "delivery_days": ("delivery_days", "lead_time_days", "交付天数"),
}


def assess_operational_risk(
    company_name: str,
    dimension: OperationalDimension,
) -> dict[str, Any]:
    """Assess quality or delivery from formal current snapshots only."""
    db = get_db()
    master = _find_master(db, company_name)
    if not master:
        return {
            "assessment_status": "missing_supplier",
            "dimension": dimension,
            "company_name": company_name,
            "risk_level": None,
            "risk_score": None,
            "data_coverage": {"available_fields": [], "required_fields": _required_fields(dimension), "coverage_ratio": 0.0},
            "reason": "未在当前供应商主数据快照中找到该供应商。",
        }

    supplier_code = str(master.get("supplier_code") or "")
    rows = [
        row for row in db["supplier_transaction_snapshots"].find({
            "supplier_code": supplier_code,
            "sync_status": "current",
            "data_mode": "real",
            "eligible_for_formal_assessment": True,
        })
        if isinstance(row, dict)
    ]
    if not rows:
        return _missing_data(company_name, dimension, "没有可用于正式评估的真实且校验通过的交易快照。")

    months = [str(row.get("snapshot_month")) for row in rows if row.get("snapshot_month")]
    latest_month = max(months) if months else None
    latest_rows = [row for row in rows if not latest_month or str(row.get("snapshot_month")) == latest_month]
    metrics = _collect_metrics(latest_rows)
    required = _required_fields(dimension)
    available = [field for field in required if metrics.get(field) is not None]
    coverage_ratio = round(len(available) / len(required), 2) if required else 0.0
    if not _has_sufficient_metrics(dimension, metrics):
        return {
            "assessment_status": "missing_data",
            "dimension": dimension,
            "company_name": company_name,
            "period": latest_month,
            "risk_level": None,
            "risk_score": None,
            "data_coverage": {
                "available_fields": available,
                "required_fields": required,
                "coverage_ratio": coverage_ratio,
            },
            "reason": "当前快照缺少足以计算该维度的质量/交付指标，不能推断低风险。",
        }

    score = _score(dimension, metrics)
    level = "high" if score >= 60 else "medium" if score >= 30 else "low"
    facts = {key: value for key, value in metrics.items() if value is not None}
    return {
        "assessment_status": "partial",
        "dimension": dimension,
        "company_name": company_name,
        "supplier_code": supplier_code,
        "period": latest_month,
        "risk_score": score,
        "risk_level": level,
        "data_coverage": {
            "available_fields": available,
            "required_fields": required,
            "coverage_ratio": coverage_ratio,
        },
        "metrics": facts,
        "evidence": [{
            "source": "feishu_transaction_snapshot",
            "period": latest_month,
            "data_mode": "formal",
            "facts": facts,
        }],
    }


def _find_master(db: Any, company_name: str) -> dict[str, Any] | None:
    collection = db["supplier_master_snapshots"]
    for query in (
        {"name": company_name, "sync_status": "current"},
        {"supplier_id": company_name, "sync_status": "current"},
        {"supplier_code": company_name, "sync_status": "current"},
    ):
        found = collection.find_one(query)
        if found:
            return found
    return None


def _required_fields(dimension: OperationalDimension) -> list[str]:
    if dimension == "quality":
        return ["quality_rate"]
    return ["on_time_rate"]


def _collect_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, float | None]:
    values: dict[str, list[float]] = {key: [] for key in _FIELD_ALIASES}
    for row in rows:
        for canonical, aliases in _FIELD_ALIASES.items():
            value = next((_number(row.get(alias)) for alias in aliases if _number(row.get(alias)) is not None), None)
            if value is not None:
                values[canonical].append(value)
    return {key: round(sum(items) / len(items), 6) if items else None for key, items in values.items()}


def _has_sufficient_metrics(dimension: OperationalDimension, metrics: dict[str, float | None]) -> bool:
    if dimension == "quality":
        return metrics.get("quality_rate") is not None or (
            metrics.get("defect_rate") is not None and metrics.get("received_qty") is not None
        )
    return metrics.get("on_time_rate") is not None or (
        metrics.get("delayed_qty") is not None and metrics.get("ordered_qty") not in (None, 0)
    )


def _score(dimension: OperationalDimension, metrics: dict[str, float | None]) -> float:
    if dimension == "quality":
        rate = metrics.get("quality_rate")
        if rate is None:
            rate = 1.0 - float(metrics["defect_rate"]) / max(float(metrics["received_qty"]), 1.0)
    else:
        rate = metrics.get("on_time_rate")
        if rate is None:
            rate = 1.0 - float(metrics["delayed_qty"]) / max(float(metrics["ordered_qty"]), 1.0)
    normalized = max(0.0, min(1.0, float(rate)))
    return round((1.0 - normalized) * 100, 2)


def _missing_data(company_name: str, dimension: OperationalDimension, reason: str) -> dict[str, Any]:
    return {
        "assessment_status": "missing_data",
        "dimension": dimension,
        "company_name": company_name,
        "risk_level": None,
        "risk_score": None,
        "data_coverage": {"available_fields": [], "required_fields": _required_fields(dimension), "coverage_ratio": 0.0},
        "reason": reason,
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["assess_operational_risk"]
