"""Reproduce product-review findings offline; never open a database connection.

Run from backend: .venv/bin/python ../docs/reviews/product_review_probes.py
The output describes observed defects, not successful product acceptance.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.domains.alert import review_tasks, service as monitoring
from app.domains.risk import operational_risk_service as operational
from app.domains.risk import predictor, sanctions_service, scenario_service


class MemoryCollection:
    def __init__(self, single=None, rows=()):
        self.single = single
        self.rows = list(rows)

    def find_one(self, *args, **kwargs):
        return self.single

    def find(self, *args, **kwargs):
        return self

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(self.rows)


class MemoryDatabase:
    def __init__(self, collections=None):
        self.collections = collections or {}

    def __getitem__(self, name):
        return self.collections.get(name, MemoryCollection())


def operational_result(dimension, **metrics):
    db = MemoryDatabase({
        "supplier_master_snapshots": MemoryCollection({"supplier_code": "AUDIT-ONLY"}),
        "supplier_transaction_snapshots": MemoryCollection(rows=[{
            "snapshot_month": "2026-10", **metrics,
        }]),
    })
    with patch.object(operational, "get_db", return_value=db):
        result = operational.assess_operational_risk("离线审查样本", dimension)
    return {key: result[key] for key in ("risk_score", "risk_level", "metrics")}


results = {"baseline": "88954645", "mode": "offline mocked sources; no database or provider calls"}
results["operational_units"] = {
    "ratio_0_952": operational_result("quality", quality_rate=0.952),
    "percent_95_2": operational_result("quality", quality_rate=95.2),
    "defect_ratio_0_048_of_10000": operational_result("quality", defect_rate=0.048, received_qty=10000),
    "delivery_ratio_0_70": operational_result("delivery", on_time_rate=0.70),
}
results["weighted_quality"] = {
    "input": [{"quality_rate": 0.5, "received_qty": 100}, {"quality_rate": 1.0, "received_qty": 9900}],
    "observed": operational._collect_metrics([
        {"quality_rate": 0.5, "received_qty": 100}, {"quality_rate": 1.0, "received_qty": 9900},
    ]),
    "expected_rate_if_received_qty_is_inspected_denominator": (50 + 9900) / 10000,
}
results["review_authorization"] = {}
for label, user_id, role in [
    ("requester", "requester-id", "purchaser"),
    ("different_user_even_if_manager_in_responsibility_table", "manager-id", "purchaser"),
    ("administrator", "admin-id", "admin"),
]:
    try:
        review_tasks._authorize({"requested_by": "requester-id"}, user_id, role)
        result = "allowed by task ownership gate"
    except PermissionError:
        result = "denied by task ownership gate"
    results["review_authorization"][label] = result

with patch.object(sanctions_service, "get_db", return_value=MemoryDatabase()), patch.object(sanctions_service, "get_baseinfo", return_value=None):
    results["sanctions_empty_sources"] = sanctions_service.check_sanctions("离线审查样本零部件有限公司")
    results["sanctions_short_alias"] = sanctions_service.check_sanctions("长江离线审查样本零部件有限公司")

with patch.object(scenario_service, "get_db", return_value=MemoryDatabase()), patch.object(scenario_service, "get_baseinfo", return_value={"name": "离线样本"}), patch.object(scenario_service, "get_supply_dependencies", return_value=[]), patch.object(scenario_service, "analyze_contagion", return_value={}):
    results["scenario_without_dependency_or_snapshot"] = scenario_service.simulate("离线样本")

db = MemoryDatabase({"sentiment_results": MemoryCollection({"analyzed_at": "2020-01-01"})})
target = {"identity_status": "verified", "company_name": "离线样本"}
snapshot = {"checked_at": "2020-01-01", "financial": {"period": "2019"}, "risk_score": 95, "risk_level": "低风险"}
with patch.object(monitoring, "_has_transaction_data", return_value=True):
    coverage = monitoring._data_coverage(db, target, snapshot)
    results["stale_coverage"] = {
        "coverage": coverage,
        "next_action": monitoring._next_action(target, snapshot, {"status": "stable"}, coverage),
    }

db = MemoryDatabase({"alert_snapshots": MemoryCollection(rows=[{"risk_score": 95}] * 3)})
with patch.object(predictor, "get_db", return_value=db), patch.object(predictor, "get_baseinfo", return_value={"name": "离线样本"}), patch.object(predictor, "get_financial_metrics", return_value=None):
    results["equal_scores_prediction"] = predictor.predict_company("离线样本")

print(json.dumps(results, ensure_ascii=False, indent=2))
