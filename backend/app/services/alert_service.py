from datetime import datetime, timezone

from app.db.mongo import get_db
from app.schemas import RiskCalculateResponse


def save_snapshot(company_name: str, result: RiskCalculateResponse) -> None:
    db = get_db()
    doc = {
        "company_name": company_name,
        "checked_at": datetime.now(timezone.utc),
        "risk_score": result.risk_score,
        "risk_level": result.risk_level,
        "risk_detail": result.risk_detail,
        "financial": result.financial.model_dump() if result.financial else None,
    }
    db["alert_snapshots"].insert_one(doc)


def get_latest_snapshot(company_name: str) -> dict | None:
    db = get_db()
    return db["alert_snapshots"].find_one(
        {"company_name": company_name},
        sort=[("checked_at", -1)],
    )


def detect_changes(company_name: str) -> dict:
    """Compare latest snapshot with second-latest, return changes."""
    db = get_db()
    snapshots = list(
        db["alert_snapshots"]
        .find({"company_name": company_name})
        .sort("checked_at", -1)
        .limit(2)
    )
    if len(snapshots) < 2:
        return {"company_name": company_name, "changed": False, "changes": []}

    new = snapshots[0]
    old = snapshots[1]
    changes = []

    def compare(field, old_val, new_val):
        if old_val != new_val:
            changes.append({"field": field, "old": old_val, "new": new_val})

    nd = new.get("risk_detail") or {}
    od = old.get("risk_detail") or {}
    compare("诉讼数量", od.get("lawsuit_count"), nd.get("lawsuit_count"))
    compare("被执行记录", od.get("executed_count"), nd.get("executed_count"))
    compare("失信记录", od.get("dishonesty_count"), nd.get("dishonesty_count"))
    compare("经营异常", od.get("abnormal_operation_count"), nd.get("abnormal_operation_count"))
    compare("行政处罚", od.get("administrative_penalty_count"), nd.get("administrative_penalty_count"))

    if not od.get("major_lawsuit") and nd.get("major_lawsuit"):
        changes.append({"field": "重大诉讼", "old": False, "new": True})

    compare("风险评分", old.get("risk_score"), new.get("risk_score"))
    compare("风险等级", old.get("risk_level"), new.get("risk_level"))

    nf = new.get("financial") or {}
    of = old.get("financial") or {}
    if of and nf:
        if (of.get("net_profit_growth") or 0) > 0 and (nf.get("net_profit_growth") or 0) < 0:
            changes.append({"field": "净利润", "old": "正增长", "new": "转为负增长"})
        compare("资产负债率", of.get("debt_ratio"), nf.get("debt_ratio"))

    severity = "critical" if any(
        "被执行" in c["field"] or "失信" in c["field"] or "重大诉讼" in c["field"]
        for c in changes
    ) else "warning" if changes else "normal"

    if changes:
        db["alerts"].insert_one({
            "company_name": company_name,
            "created_at": datetime.now(timezone.utc),
            "changes": changes,
            "severity": severity,
        })

    return {
        "company_name": company_name,
        "changed": len(changes) > 0,
        "severity": severity,
        "changes": changes,
        "previous": old.get("checked_at").isoformat() if old.get("checked_at") else None,
        "current": new.get("checked_at").isoformat() if new.get("checked_at") else None,
    }


def add_to_watchlist(company_name: str) -> dict:
    db = get_db()
    db["watchlist"].update_one(
        {"company_name": company_name},
        {"$set": {"company_name": company_name, "added_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return {"company_name": company_name, "status": "watching"}


def remove_from_watchlist(company_name: str) -> dict:
    db = get_db()
    db["watchlist"].delete_one({"company_name": company_name})
    return {"company_name": company_name, "status": "removed"}


def get_watchlist() -> list[str]:
    db = get_db()
    return [doc["company_name"] for doc in db["watchlist"].find()]


def refresh_company(company_name: str) -> dict:
    """Paid channel: call Tianyancha API, re-assess, compare."""
    from app.schemas import RiskAssessRequest
    from app.services.risk_service import assess_risk
    from app.services.tianyancha_client import fetch_company

    fetch_company(company_name)
    assess_risk(RiskAssessRequest(company_name=company_name))
    return detect_changes(company_name)
