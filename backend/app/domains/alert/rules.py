"""
自定义告警规则引擎。

默认规则（全局）：
- 诉讼新增 ≥ 1 → warning
- 被执行新增 ≥ 1 → critical
- 失信新增 ≥ 1 → critical
- 经营异常新增 ≥ 1 → warning
- 行政处罚新增 ≥ 1 → warning
- 重大诉讼 false→true → warning
- 风险评分上涨 ≥ 10 → warning
- 资产负债率上涨 ≥ 5% → warning
- 净利润转负 → critical

每个供应商可覆盖全局规则。
"""

from datetime import datetime, timezone

from app.db.mongo import get_db

DEFAULT_RULES = [
    {"field": "诉讼数量", "operator": "increase", "threshold": 1, "severity": "warning"},
    {"field": "被执行记录", "operator": "increase", "threshold": 1, "severity": "critical"},
    {"field": "失信记录", "operator": "increase", "threshold": 1, "severity": "critical"},
    {"field": "经营异常", "operator": "increase", "threshold": 1, "severity": "warning"},
    {"field": "行政处罚", "operator": "increase", "threshold": 1, "severity": "warning"},
    {"field": "重大诉讼", "operator": "become_true", "threshold": 0, "severity": "warning"},
    {"field": "风险评分", "operator": "increase", "threshold": 10, "severity": "warning"},
    {"field": "净利润", "operator": "become_negative", "threshold": 0, "severity": "critical"},
]


def get_rules(company_name: str | None = None) -> list[dict]:
    db = get_db()
    if company_name:
        doc = db["alert_rules"].find_one({"company_name": company_name})
        if doc:
            return doc.get("rules", DEFAULT_RULES)
    return DEFAULT_RULES


def set_rules(company_name: str | None, rules: list[dict]) -> dict:
    db = get_db()
    key = company_name or "__global__"
    db["alert_rules"].update_one(
        {"company_name": key},
        {"$set": {"company_name": key, "rules": rules, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return {"company_name": key, "rules": rules}


def evaluate_changes(rules: list[dict], changes: list[dict]) -> list[dict]:
    """Filter changes through rules and assign severity."""
    triggered = []
    for change in changes:
        for rule in rules:
            if change["field"] != rule["field"]:
                continue
            old_val = change.get("old", 0) or 0
            new_val = change.get("new", 0) or 0

            if rule["operator"] == "increase":
                if isinstance(new_val, (int, float)) and isinstance(old_val, (int, float)):
                    if new_val - old_val >= rule["threshold"]:
                        triggered.append({**change, "severity": rule["severity"]})
            elif rule["operator"] == "become_true":
                if not old_val and new_val:
                    triggered.append({**change, "severity": rule["severity"]})
            elif rule["operator"] == "become_negative":
                if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
                    if old_val >= 0 and new_val < 0:
                        triggered.append({**change, "severity": rule["severity"]})

    return triggered
