"""
风险预测模型 - 基于趋势信号的早期预警。

通过分析多期数据趋势，预测供应商未来 6-12 个月内风险恶化的概率。
不使用 ML 模型，而是基于可解释的规则评分，每个信号可追溯。
"""

from app.db.mongo import get_db
from app.domains.risk.repo_company import get_baseinfo
from app.domains.risk.repo_financial import get_financial_metrics


def predict_company(
    company_name: str,
    *,
    monitor_target_id: str | None = None,
    user_id: str | None = None,
    user_role: str | None = None,
) -> dict | None:
    """Predict deterioration risk for a single company."""
    if user_id is not None or user_role is not None:
        from app.domains.alert.service import get_watchlist_targets

        visible = get_watchlist_targets(user_id, user_role)
        if not any(
            str(target.get("company_name") or "") == company_name
            for target in visible
        ):
            return None

    profile = get_baseinfo(company_name)
    if not profile:
        return None

    fin = get_financial_metrics(company_name)
    signals = []
    score = 0

    # ---- trend signals from financial data ----
    if fin:
        if fin.revenue_trend < -0.03:
            score += 3
            signals.append({"signal": "营收持续下滑", "score": 3, "detail": f"3年趋势斜率 {fin.revenue_trend:.3f}"})
        elif fin.revenue_trend < -0.01:
            score += 1
            signals.append({"signal": "营收轻微下滑", "score": 1})

        if fin.debt_trend > 0.03:
            score += 3
            signals.append({"signal": "负债持续上升", "score": 3, "detail": f"3年趋势 +{fin.debt_trend*100:.1f}%"})
        elif fin.debt_trend > 0.01:
            score += 1
            signals.append({"signal": "负债轻微上升", "score": 1})

        if fin.net_profit_trend < -0.05:
            score += 2
            signals.append({"signal": "净利持续恶化", "score": 2})
        elif fin.net_profit_trend < -0.02:
            score += 1
            signals.append({"signal": "净利轻微下滑", "score": 1})

        if fin.current_ratio > 0 and fin.current_ratio < 0.8:
            score += 2
            signals.append({"signal": "流动性紧张", "score": 2, "detail": f"流动比率 {fin.current_ratio:.2f}"})
        elif fin.current_ratio > 0 and fin.current_ratio < 1.2:
            score += 1
            signals.append({"signal": "流动性偏弱", "score": 1})

        if fin.ar_turnover_days > 90:
            score += 2
            signals.append({"signal": "回款恶化", "score": 2, "detail": f"应收周转 {fin.ar_turnover_days:.0f}天"})

        if fin.recurring_profit_ratio > 0 and fin.recurring_profit_ratio < 0.5:
            score += 2
            signals.append({"signal": "利润质量差", "score": 2, "detail": f"扣非占比 {fin.recurring_profit_ratio*100:.0f}%"})

    # ---- trend from snapshot history ----
    db = get_db()
    snapshot_query = (
        {"monitor_target_id": monitor_target_id}
        if monitor_target_id
        else {"company_name": company_name}
    )
    snaps = list(
        db["alert_snapshots"]
        .find(snapshot_query)
        .sort("checked_at", -1)
        .limit(5)
    )
    if not snaps and monitor_target_id:
        # Compatibility for snapshots written before the monitor target migration.
        snaps = list(
            db["alert_snapshots"]
            .find({"company_name": company_name})
            .sort("checked_at", -1)
            .limit(5)
        )
    if len(snaps) >= 2:
        scores = [s.get("risk_score", 0) for s in snaps]
        if len(scores) >= 3 and all(scores[i] <= scores[i+1] for i in range(len(scores)-1)):
            score += 1
            signals.append({"signal": "安全评分持续下降", "score": 1})
        if snaps[0].get("risk_score", 100) < 40:
            score += 2
            signals.append({"signal": "已处于高风险区间", "score": 2})

    # ---- judicial trends ----
    if len(snaps) >= 2:
        rd_new = (snaps[0].get("risk_detail") or {})
        rd_old = (snaps[-1].get("risk_detail") or {})
        lawsuit_new = rd_new.get("lawsuit_count", 0)
        lawsuit_old = rd_old.get("lawsuit_count", 0)
        if lawsuit_new > lawsuit_old + 5:
            score += 2
            signals.append({"signal": "诉讼快速增长", "score": 2, "detail": f"{lawsuit_old}→{lawsuit_new}"})

    # ---- probability mapping ----
    if score >= 7:
        probability = "high"
        label = "高概率恶化"
    elif score >= 4:
        probability = "medium"
        label = "可能恶化"
    else:
        probability = "low"
        label = "大概率稳定"

    if not fin and len(snaps) < 2:
        return {
            "company_name": company_name,
            "probability": "unknown",
            "label": "当前未覆盖",
            "warning_score": 0,
            "max_score": 14,
            "signals": [],
            "has_data": False,
        }

    return {
        "company_name": company_name,
        "probability": probability,
        "label": label,
        "warning_score": score,
        "max_score": 14,
        "signals": signals,
        "has_data": True,
    }


def predict_all(user_id: str | None = None, user_role: str | None = None) -> list[dict]:
    """Predict for all companies in watchlist."""
    from app.domains.alert.service import get_watchlist_targets

    targets = get_watchlist_targets(user_id, user_role)
    results = []
    for target in targets:
        name = target.get("company_name", "")
        pred = predict_company(
            name,
            monitor_target_id=target.get("monitor_target_id"),
        )
        if pred:
            results.append({
                **pred,
                "monitor_target_id": target.get("monitor_target_id"),
                "target_type": target.get("target_type"),
            })
        else:
            results.append({
                "company_name": name,
                "monitor_target_id": target.get("monitor_target_id"),
                "target_type": target.get("target_type"),
                "probability": "unknown",
                "label": "无数据",
                "has_data": False,
            })
    results.sort(key=lambda r: r.get("warning_score", 0), reverse=True)
    return results
