"""商务风险 P0：基于飞书月度交易快照输出可审计的依赖信号。

P0 仅正式启用“供应依赖与可替代性”（商务风险模型权重 30%）。
合同、结算和价格变化当前只作为观察信号，绝不拼接成完整商务分数。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.core.config import settings
from app.db.mongo import get_db


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _find_current_master(db: Any, supplier_reference: str) -> dict[str, Any] | None:
    collection = db["supplier_master_snapshots"]
    for query in (
        {"supplier_id": supplier_reference, "sync_status": "current"},
        {"_id": supplier_reference, "sync_status": "current"},
        {"supplier_code": supplier_reference, "sync_status": "current"},
        {"name": supplier_reference, "sync_status": "current"},
    ):
        document = collection.find_one(query)
        if document:
            return document
    return None


def _risk_level(spend_share: float, supplier_count: int) -> str:
    if supplier_count <= 1 or spend_share >= 0.7:
        return "high"
    if supplier_count <= 2 or spend_share >= 0.4:
        return "medium"
    return "low"


def _is_demo_enabled() -> bool:
    """Synthetic snapshots are available only in an explicitly enabled dev environment."""
    return settings.DEBUG and settings.BUSINESS_RISK_DEMO_ENABLED


def _is_assessment_row(row: dict[str, Any], *, data_mode: str) -> bool:
    if row.get("sync_status") != "current" or row.get("data_mode") != data_mode:
        return False
    if data_mode == "real":
        return row.get("eligible_for_formal_assessment") is True
    return (
        row.get("data_quality_status") == "valid"
        and not row.get("validation_errors")
        and not row.get("data_quality_issues")
    )


def _contract_signal(rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = defaultdict(int)
    for row in rows:
        statuses[str(row.get("contract_status") or "unknown")] += 1
    return {
        "status": "observed" if rows else "missing",
        "active_rows": statuses["active"],
        "expiring_rows": statuses["expiring"],
        "expired_rows": statuses["expired"],
        "unsigned_rows": statuses["unsigned"],
        "unknown_rows": statuses["unknown"],
        "note": "P0 仅展示合同状态，不计入正式商务风险分数。",
    }


def _settlement_signal(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actual = sum(value for row in rows if (value := _as_number(row.get("actual_settlement_amount"))) is not None)
    unsettled = sum(value for row in rows if (value := _as_number(row.get("unsettled_amount"))) is not None)
    if actual <= 0:
        return {"status": "missing", "unsettled_amount": None, "unsettled_ratio": None}
    return {
        "status": "observed",
        "unsettled_amount": round(unsettled, 2),
        "unsettled_ratio": round(unsettled / actual, 4),
        "note": "P0 仅展示结算暴露，不计入正式商务风险分数。",
    }


def _price_signal(rows: list[dict[str, Any]], prior_rows: list[dict[str, Any]]) -> dict[str, Any]:
    def weighted_average(items: list[dict[str, Any]]) -> float | None:
        weighted_sum = 0.0
        total_quantity = 0.0
        for item in items:
            price = _as_number(item.get("unit_price"))
            quantity = _as_number(item.get("received_qty"))
            if price is None or quantity is None or quantity <= 0:
                continue
            weighted_sum += price * quantity
            total_quantity += quantity
        return weighted_sum / total_quantity if total_quantity else None

    current = weighted_average(rows)
    prior = weighted_average(prior_rows)
    if current is None or prior is None or prior == 0:
        return {"status": "missing", "current_weighted_price": current, "change_ratio": None}
    return {
        "status": "observed",
        "current_weighted_price": round(current, 6),
        "prior_weighted_price": round(prior, 6),
        "change_ratio": round((current - prior) / prior, 4),
        "note": "P0 仅展示价格变化，不计入正式商务风险分数。",
    }


def _shift_month(month: str, offset: int) -> str:
    year, month_number = (int(part) for part in month.split("-"))
    zero_based = year * 12 + month_number - 1 + offset
    return f"{zero_based // 12:04d}-{zero_based % 12 + 1:02d}"


def _aggregate_monthly_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    monthly: dict[str, dict[str, float]] = {}
    for row in rows:
        month = row.get("snapshot_month")
        if not isinstance(month, str) or not month:
            continue
        item = monthly.setdefault(
            month,
            {"actual_settlement_amount": 0.0, "received_record_count": 0.0},
        )
        amount = _as_number(row.get("actual_settlement_amount"))
        count = _as_number(row.get("received_record_count"))
        if amount is not None:
            item["actual_settlement_amount"] += amount
        if count is not None:
            item["received_record_count"] += count
    return monthly


def _change_signal(current: float, previous: float | None) -> dict[str, Any]:
    if previous is None or previous == 0:
        return {
            "status": "missing",
            "current": round(current, 2),
            "previous": round(previous, 2) if previous is not None else None,
            "change_ratio": None,
        }
    return {
        "status": "observed",
        "current": round(current, 2),
        "previous": round(previous, 2),
        "change_ratio": round((current - previous) / abs(previous), 4),
    }


def _assess_supplier_month_summary(
    db: Any,
    master: dict[str, Any],
    supplier_rows: list[dict[str, Any]],
    *,
    assessment_data_mode: str,
) -> dict[str, Any]:
    """Assess internal procurement exposure at the available supplier-month grain."""
    supplier_code = str(master["supplier_code"])
    monthly = _aggregate_monthly_summary(supplier_rows)
    if not monthly:
        return {
            "assessment_status": "missing_data",
            "assessment_scope": "business_p0_supplier_month_summary",
            "supplier": {
                "supplier_id": master.get("supplier_id"),
                "supplier_code": supplier_code,
                "name": master.get("name"),
            },
            "formal_business_score": None,
            "reason": "供应商月度汇总中没有可用月份。",
            "required_data_mode": "real",
        }

    latest_month = max(monthly)
    previous_month = _shift_month(latest_month, -1)
    latest = monthly[latest_month]
    previous = monthly.get(previous_month)
    latest_amount = latest["actual_settlement_amount"]
    latest_receipts = latest["received_record_count"]

    all_rows = list(db["supplier_transaction_snapshots"].find({
        "sync_status": "current",
        "data_mode": assessment_data_mode,
    }))
    comparison_rows = [
        row
        for row in all_rows
        if row.get("data_granularity") == "supplier_month"
        and row.get("snapshot_month") == latest_month
        and _is_assessment_row(row, data_mode=assessment_data_mode)
    ]
    supplier_totals: dict[str, float] = defaultdict(float)
    for row in comparison_rows:
        amount = _as_number(row.get("actual_settlement_amount"))
        code = row.get("supplier_code")
        if amount is not None and code:
            supplier_totals[str(code)] += amount
    positive_total = sum(amount for amount in supplier_totals.values() if amount > 0)
    settlement_share = (
        latest_amount / positive_total
        if latest_amount > 0 and positive_total > 0
        else None
    )
    comparison_supplier_count = sum(
        1 for amount in supplier_totals.values() if amount > 0
    )
    exposure_level = (
        _risk_level(settlement_share, comparison_supplier_count)
        if settlement_share is not None
        else "unknown"
    )

    window_months = [_shift_month(latest_month, offset) for offset in range(-11, 1)]
    present_months = [month for month in window_months if month in monthly]
    missing_months = [month for month in window_months if month not in monthly]
    trailing_amount = sum(
        monthly[month]["actual_settlement_amount"] for month in present_months
    )
    trailing_receipts = int(sum(
        monthly[month]["received_record_count"] for month in present_months
    ))
    negative_months = [
        month
        for month in present_months
        if monthly[month]["actual_settlement_amount"] < 0
    ]
    settlement_without_receipts = [
        month
        for month in present_months
        if monthly[month]["received_record_count"] == 0
        and monthly[month]["actual_settlement_amount"] != 0
    ]
    amount_trend = _change_signal(
        latest_amount,
        previous["actual_settlement_amount"] if previous else None,
    )
    receipt_trend = _change_signal(
        latest_receipts,
        previous["received_record_count"] if previous else None,
    )

    evidence_facts = {
        "exposure_level": exposure_level,
        "settlement_share": round(settlement_share, 8) if settlement_share is not None else None,
        "latest_actual_settlement_amount": round(latest_amount, 2),
        "latest_received_record_count": int(latest_receipts),
        "comparison_supplier_count": comparison_supplier_count,
        "missing_month_count": len(missing_months),
        "settlement_change_ratio": amount_trend["change_ratio"],
        "receipt_record_change_ratio": receipt_trend["change_ratio"],
        "settlement_without_receipts_month_count": len(settlement_without_receipts),
        "monthly_trend": [
            {
                "month": month,
                "actual_settlement_amount": round(monthly[month]["actual_settlement_amount"], 2),
                "received_record_count": int(monthly[month]["received_record_count"]),
            }
            for month in present_months
        ],
    }
    evidence = [{
        "source": "feishu_supplier_monthly_summary",
        "period": latest_month,
        "claim": "内部采购敞口基于供应商月度实结算金额计算；收货记录数仅表示源明细行数。",
        "data_mode": assessment_data_mode,
        "rows": len(comparison_rows),
        "facts": evidence_facts,
    }]
    return {
        "assessment_status": "partial",
        "assessment_scope": "business_p0_supplier_month_summary",
        "assessment_data_mode": "demo" if assessment_data_mode == "synthetic" else "formal",
        "decision_usable": assessment_data_mode == "real",
        "supplier": {
            "supplier_id": master.get("supplier_id"),
            "supplier_code": supplier_code,
            "name": master.get("name"),
        },
        "period": latest_month,
        "scope": {
            "data_granularity": "supplier_month",
            "comparison_basis": "同月已关联供应商的正向实结算金额",
            "currency": None,
            "amount_basis": "源表实结算金额口径",
        },
        "coverage": None,
        "formal_business_score": None,
        "enabled_dimension": {
            "name": "内部采购敞口",
            "model_weight": 0.0,
            "risk_level": exposure_level,
            "exposure_level": exposure_level,
            "supplier_spend_share": round(settlement_share, 8) if settlement_share is not None else 0,
            "settlement_share": round(settlement_share, 8) if settlement_share is not None else None,
            "supplier_received_amount": round(latest_amount, 2),
            "category_total_received_amount": round(positive_total, 2),
            "latest_actual_settlement_amount": round(latest_amount, 2),
            "comparison_actual_settlement_amount": round(positive_total, 2),
            "active_supplier_count": comparison_supplier_count,
            "single_source": False,
        },
        "observed_signals": {
            "contract": {"status": "missing"},
            "settlement": {
                "status": "observed",
                "actual_settlement_amount": round(latest_amount, 2),
                "trailing_12_month_amount": round(trailing_amount, 2),
                "change_ratio": amount_trend["change_ratio"],
                "negative_months": negative_months,
                "settlement_without_receipts_months": settlement_without_receipts,
                "unsettled_amount": None,
                "unsettled_ratio": None,
            },
            "receipts": {
                "status": "observed",
                "received_record_count": int(latest_receipts),
                "trailing_12_month_count": trailing_receipts,
                "change_ratio": receipt_trend["change_ratio"],
            },
            "price": {"status": "missing", "change_ratio": None},
            "data_continuity": {
                "status": "complete" if not missing_months else "incomplete",
                "present_months": len(present_months),
                "expected_months": 12,
                "missing_months": missing_months,
            },
        },
        "not_formally_enabled_dimensions": [
            "供应依赖与可替代性",
            "价格与成本",
            "合同与条款",
            "未结算暴露",
            "商务合作稳定性",
        ],
        "limitations": [
            "当前数据只支持判断内部采购敞口，不能单独证明供应商自身风险或可替代性。",
            "缺少采购品类、物料、基地和币种口径，不能进行同品类集中度或价格比较。",
            "收货记录数是源明细行数，不代表送货批次、交付频次或收货数量。",
            "历史月份缺失与数值为零严格区分，缺失月份不参与趋势计算。",
        ] + ([f"最近 12 个自然月缺少 {len(missing_months)} 个月数据。"] if missing_months else []),
        "evidence": evidence,
    }


def assess_business_risk_p0(
    supplier_reference: str,
    *,
    category_code: str | None = None,
    purchasing_org_code: str | None = None,
    base: str | None = None,
) -> dict[str, Any]:
    """Return the formal P0 dependency assessment and explicitly partial signals."""
    db = get_db()
    master = _find_current_master(db, supplier_reference)
    if not master:
        return {
            "assessment_status": "missing_supplier",
            "assessment_scope": "business_p0_transaction_snapshot",
            "supplier_reference": supplier_reference,
            "formal_business_score": None,
            "reason": "未在当前飞书供应商主数据快照中找到该供应商。",
        }

    supplier_code = str(master["supplier_code"])
    transaction_collection = db["supplier_transaction_snapshots"]
    raw_supplier_rows = list(transaction_collection.find({
        "supplier_code": supplier_code,
        "sync_status": "current",
    }))
    assessment_data_mode = "real"
    supplier_rows = [
        row for row in raw_supplier_rows
        if _is_assessment_row(row, data_mode=assessment_data_mode)
    ]
    if not supplier_rows and _is_demo_enabled():
        assessment_data_mode = "synthetic"
        supplier_rows = [
            row for row in raw_supplier_rows
            if _is_assessment_row(row, data_mode=assessment_data_mode)
        ]
    if category_code:
        supplier_rows = [row for row in supplier_rows if row.get("category_code") == category_code]
    if purchasing_org_code:
        supplier_rows = [row for row in supplier_rows if row.get("purchasing_org_code") == purchasing_org_code]
    if base:
        supplier_rows = [row for row in supplier_rows if row.get("base") == base]
    if not supplier_rows:
        return {
            "assessment_status": "missing_data",
            "assessment_scope": "business_p0_transaction_snapshot",
            "supplier": {"supplier_id": master.get("supplier_id"), "supplier_code": supplier_code, "name": master.get("name")},
            "formal_business_score": None,
            "reason": "没有可用于正式评估的真实且校验通过的交易快照；合成、未知或无效数据已被隔离。",
            "required_data_mode": "real",
        }

    if any(row.get("data_granularity") == "supplier_month" for row in supplier_rows):
        summary_rows = [
            row for row in supplier_rows
            if row.get("data_granularity") == "supplier_month"
        ]
        return _assess_supplier_month_summary(
            db,
            master,
            summary_rows,
            assessment_data_mode=assessment_data_mode,
        )

    latest_month = max(str(row["snapshot_month"]) for row in supplier_rows if row.get("snapshot_month"))
    current_supplier_rows = [row for row in supplier_rows if row.get("snapshot_month") == latest_month]
    available_categories = sorted({
        str(row["category_code"])
        for row in current_supplier_rows
        if row.get("category_code")
    })
    if not category_code and len(available_categories) > 1:
        return {
            "assessment_status": "needs_scope",
            "assessment_scope": "business_p0_transaction_snapshot",
            "supplier": {"supplier_id": master.get("supplier_id"), "supplier_code": supplier_code, "name": master.get("name")},
            "formal_business_score": None,
            "reason": "该供应商在最新周期存在多个采购品类，请指定品类代码后计算采购集中度。",
            "available_category_codes": available_categories,
        }
    selected_category = category_code or next(
        (str(row["category_code"]) for row in current_supplier_rows if row.get("category_code")),
        None,
    )
    selected_org = purchasing_org_code or next(
        (str(row["purchasing_org_code"]) for row in current_supplier_rows if row.get("purchasing_org_code")),
        None,
    )
    selected_base = base or next((str(row["base"]) for row in current_supplier_rows if row.get("base")), None)
    selected_currency = next((str(row["currency"]) for row in current_supplier_rows if row.get("currency")), None)
    selected_basis = next((str(row["amount_basis"]) for row in current_supplier_rows if row.get("amount_basis")), None)

    scope_query = {
        "sync_status": "current",
        "snapshot_month": latest_month,
        "purchasing_org_code": selected_org,
        "base": selected_base,
        "currency": selected_currency,
        "amount_basis": selected_basis,
    }
    if selected_category:
        scope_query["category_code"] = selected_category
    scope_rows = [
        row for row in transaction_collection.find(scope_query)
        if _is_assessment_row(row, data_mode=assessment_data_mode)
    ]
    if not selected_category:
        scope_rows = [row for row in scope_rows if row.get("category_code") in (None, "")]

    supplier_spend = sum(
        amount for row in scope_rows if row.get("supplier_code") == supplier_code
        if (amount := _as_number(row.get("received_amount"))) is not None
    )
    total_spend = sum(
        amount for row in scope_rows if (amount := _as_number(row.get("received_amount"))) is not None
    )
    supplier_count = len({str(row["supplier_code"]) for row in scope_rows if row.get("supplier_code")})
    if total_spend <= 0 or supplier_spend <= 0:
        return {
            "assessment_status": "missing_data",
            "assessment_scope": "business_p0_transaction_snapshot",
            "supplier": {"supplier_id": master.get("supplier_id"), "supplier_code": supplier_code, "name": master.get("name")},
            "formal_business_score": None,
            "reason": "最新周期缺少可比较的收货金额，无法计算采购集中度。",
        }

    prior_months = sorted({str(row["snapshot_month"]) for row in supplier_rows if row.get("snapshot_month") and row.get("snapshot_month") < latest_month})
    prior_rows: list[dict[str, Any]] = []
    if prior_months:
        previous_month = prior_months[-1]
        prior_rows = [
            row for row in supplier_rows
            if row.get("snapshot_month") == previous_month
            and row.get("purchasing_org_code") == selected_org
            and row.get("base") == selected_base
            and row.get("currency") == selected_currency
            and row.get("amount_basis") == selected_basis
            and (row.get("category_code") == selected_category if selected_category else not row.get("category_code"))
        ]

    share = supplier_spend / total_spend
    dependency_level = _risk_level(share, supplier_count)
    supplier_scope_rows = [row for row in scope_rows if row.get("supplier_code") == supplier_code]
    evidence = [
        {
            "source": "feishu_transaction_snapshot",
            "period": latest_month,
            "claim": "采购集中度与可替代性基于当前评估数据模式的月度收货金额计算。",
            "data_mode": assessment_data_mode,
            "rows": len(scope_rows),
            "facts": {
                "risk_level": dependency_level,
                "supplier_spend_share": round(share, 4),
                "active_supplier_count": supplier_count,
            },
        }
    ]
    return {
        "assessment_status": "partial",
        "assessment_scope": "business_p0_transaction_snapshot",
        "assessment_data_mode": "demo" if assessment_data_mode == "synthetic" else "formal",
        "decision_usable": assessment_data_mode == "real",
        "supplier": {"supplier_id": master.get("supplier_id"), "supplier_code": supplier_code, "name": master.get("name")},
        "period": latest_month,
        "scope": {
            "category_code": selected_category,
            "purchasing_org_code": selected_org,
            "base": selected_base,
            "currency": selected_currency,
            "amount_basis": selected_basis,
        },
        "coverage": 0.30,
        "formal_business_score": None,
        "enabled_dimension": {
            "name": "供应依赖与可替代性",
            "model_weight": 0.30,
            "risk_level": dependency_level,
            "supplier_spend_share": round(share, 4),
            "supplier_received_amount": round(supplier_spend, 2),
            "category_total_received_amount": round(total_spend, 2),
            "active_supplier_count": supplier_count,
            "single_source": supplier_count == 1,
        },
        "observed_signals": {
            "contract": _contract_signal(supplier_scope_rows),
            "settlement": _settlement_signal(supplier_scope_rows),
            "price": _price_signal(supplier_scope_rows, prior_rows),
        },
        "not_formally_enabled_dimensions": ["价格与成本", "合同与条款", "交易与结算", "商务合作稳定性"],
        "limitations": [
            "商务风险完整模型尚未启用，P0 不输出完整商务风险分数。",
            "可替代性当前以同范围交易供应商数量近似，不等同于已完成资质核验的替代供应商池。",
        ] + (["来源交易快照缺少品类代码，本次集中度按采购组织、基地、币种和金额口径汇总。"] if not selected_category else []) + (["当前使用合成交易快照进行演示，不可用于正式采购决策、告警或供应商评价。"] if assessment_data_mode == "synthetic" else []),
        "evidence": evidence,
    }
