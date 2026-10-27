"""Pure scoring of frozen evidence and policy; no provider or database reads."""

from app.domains.risk.assessment_contracts import json_snapshot, validate_manifest
from app.domains.risk.assessment_policy import DIMENSIONS
from app.schemas import RiskCalculateRequest

DIMENSION_LABELS = dict(zip(DIMENSIONS, ("财务风险", "司法风险", "经营风险", "软指标")))


def score_frozen_inputs(inputs: dict, manifest: list[dict], policy: dict) -> dict:
    from app.domains.risk.service import _calc_score, _classify_industry

    inputs, policy = json_snapshot(inputs), json_snapshot(policy)
    manifest = validate_manifest(manifest)
    required = policy["required_dimensions"]
    available = {}
    for dimension in required:
        entries = [entry for entry in manifest if entry["dimension"] == dimension]
        available[dimension] = bool(entries) and all(
            entry["status"] in {"available", "empty"} for entry in entries
        )
    covered = [dimension for dimension in required if available[dimension]]
    partial_sources = any(
        entry.get("coverage_partial")
        for entry in manifest
        if entry["dimension"] in covered
    )
    hard = [dimension for dimension in covered if dimension != "soft"]
    quality = (
        "complete"
        if len(covered) == len(required) and not partial_sources
        else "partial"
    )
    if len(hard) < policy["minimum_hard_dimensions"]:
        quality = "unavailable"
    coverage = {
        "required_dimensions": required,
        "available_dimensions": covered,
        "missing_dimensions": [
            dimension for dimension in required if dimension not in covered
        ],
        "coverage_ratio": round(len(covered) / len(required), 4),
        "assessment_status": quality,
    }
    if quality == "unavailable":
        return {
            "quality": quality,
            "risk_score": None,
            "risk_level": None,
            "risk_detail": {"data_coverage": coverage},
            "score_breakdown": {},
            "financial": None,
        }
    request = RiskCalculateRequest.model_validate(inputs["request"])
    score, breakdown = _calc_score(
        request,
        inputs.get("industry_category") or _classify_industry(request.company.industry),
        include_soft_risk="soft" in required,
        frozen_inputs={**inputs, "availability": available},
        policy_snapshot=policy,
    )
    thresholds = policy["thresholds"]
    level = (
        "低风险"
        if score >= thresholds["low_risk"]
        else "中风险"
        if score >= thresholds["medium_risk"]
        else "高风险"
    )
    risk_detail = {**dict(inputs.get("risk_detail") or {}), "data_coverage": coverage}
    dimension_fields = {
        "judicial": (
            "lawsuit_count",
            "executed_count",
            "dishonesty_count",
            "major_lawsuit",
            "court_announcement_count",
            "consumption_restriction_count",
            "recent_lawsuits",
        ),
        "operational": (
            "abnormal_operation_count",
            "administrative_penalty_count",
            "legal_person_change_frequent",
            "guarantee_count",
            "pledge_count",
            "bankruptcy_count",
            "env_penalty_count",
        ),
        "financial": ("net_profit_declining", "revenue_declining"),
    }
    for dimension, fields in dimension_fields.items():
        if not available.get(dimension):
            for field in fields:
                risk_detail.pop(field, None)
    return {
        "quality": quality,
        "risk_score": score,
        "risk_level": level,
        "score_breakdown": breakdown,
        "risk_detail": risk_detail,
        "financial": request.financial.model_dump(mode="json")
        if request.financial and available.get("financial")
        else None,
        "is_listed": request.company.is_listed,
    }
