"""Versioned scoring policy; each call returns a detached snapshot."""

from app.domains.risk.assessment_contracts import json_snapshot

POLICY_ID = "supplier_safety"
POLICY_VERSION = "2026-10-19.evidence.1"
CALCULATION_VERSION = "v3-frozen-evidence-1"
SCORING_VERSION = "v3_evidence_1"
DIMENSIONS = ("financial", "judicial", "operational", "soft")


def current_policy(dimensions: list[str] | None = None) -> dict:
    required = sorted(set(dimensions or DIMENSIONS))
    if not required or set(required) - set(DIMENSIONS):
        raise ValueError("评估维度无效")
    return json_snapshot({
        "policy_id": POLICY_ID, "version": POLICY_VERSION,
        "calculation_version": CALCULATION_VERSION, "scoring_version": SCORING_VERSION,
        "score_direction": "higher_is_safer", "score_range": [0, 100],
        "required_dimensions": required, "weights": {dimension: 25 for dimension in DIMENSIONS},
        "normalizers": {"financial": 70, "judicial": 75, "operational": 45, "soft": 60},
        "thresholds": {"low_risk": 70, "medium_risk": 40, "debt_ratio": .4, "ar_turnover": 180., "current_ratio": 1.},
        "missing_policy": "exclude_and_normalize_available", "minimum_hard_dimensions": 1,
        "freshness_days": {"financial": 550, "judicial": 90, "operational": 90, "soft": 1},
        "applicability": "canonical_company_and_purchase_scope",
        "industry_thresholds": {
            "重工业": {"debt_ratio": .2, "ar_turnover": 70, "current_ratio": -.2},
            "建筑地产": {"debt_ratio": .25, "ar_turnover": 100, "current_ratio": -.1},
            "科技": {"debt_ratio": -.1, "ar_turnover": -60, "current_ratio": .1},
            "商贸服务": {"debt_ratio": .05, "ar_turnover": -30, "current_ratio": -.05},
            "制造业": {"debt_ratio": .05, "ar_turnover": 0, "current_ratio": 0},
        },
        "industry_offsets": {"重工业": 3, "建筑地产": 3, "科技": -2, "商贸服务": 0, "制造业": 0},
    })
