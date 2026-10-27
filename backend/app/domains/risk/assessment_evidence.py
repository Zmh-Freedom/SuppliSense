"""Collect and content-address evidence before the immutable scoring boundary."""

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.mongo import get_db
from app.domains.risk.assessment_contracts import canonical_hash, json_snapshot
from app.schemas import RiskCalculateRequest
from app.schemas.risk import RiskInfo

JUDICIAL_SOURCES = (
    "lawSuit",
    "courtRegister",
    "executedPerson",
    "dishonesty",
    "courtAnnouncement",
    "consumptionRestriction",
)
OPERATIONAL_SOURCES = ("abnormal", "punishmentInfo")


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items() if key != "_id"}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, str):
        try:
            return _timestamp(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def provider_receipt(document: dict | None) -> dict:
    if not isinstance(document, dict):
        return {"status": "unavailable", "reason": "not_queried"}
    body = document.get("items") or document.get("item") or document
    if not isinstance(body, dict):
        return {"status": "unavailable", "reason": "invalid_payload"}
    if any(
        body.get(key) not in (None, 0, "0", 200, "200")
        for key in ("error_code", "errorCode", "code")
    ):
        return {"status": "unavailable", "reason": "provider_failed"}
    explicit = document.get("_query_receipt") or {}
    if explicit.get("status") not in (None, "success"):
        return {"status": "unavailable", "reason": "provider_failed"}
    result = body.get("result") if isinstance(body.get("result"), dict) else body
    count = result.get("total", body.get("total"))
    if count is None:
        for key in ("items", "list", "riskList"):
            if isinstance(result.get(key), list):
                count = len(result[key])
                break
    if isinstance(count, (int, float)) and not isinstance(count, bool) and count >= 0:
        return {"status": "success", "record_count": int(count)}
    return {"status": "unavailable", "reason": "missing_query_receipt"}


def make_evidence(
    assessment: dict,
    dimension: str,
    payload: dict,
    *,
    status: str,
    source: str,
    policy: dict,
    observed_at: Any = None,
    query_receipt: dict | None = None,
    coverage_partial: bool = False,
) -> tuple[dict, dict]:
    payload = _safe(payload)
    digest = canonical_hash(payload)
    now = datetime.now(timezone.utc)
    observed = _timestamp(observed_at)
    invalid_observation = observed_at not in (None, "") and observed is None
    future_observation = observed is not None and observed > now
    if invalid_observation or future_observation:
        status = "unavailable"
    elif observed and now - observed > timedelta(
        days=policy["freshness_days"][dimension]
    ):
        status = "stale"
    # An old empty response without a source timestamp cannot prove absence now.
    if status == "empty" and observed is None:
        status = "unavailable"
    reference = f"assessment:{assessment['id']}:{dimension}:{digest}"
    entry = {
        "evidence_id": f"{dimension}:{digest}",
        "dimension": dimension,
        "source": source,
        "status": status,
        "content_hash": digest,
        "raw_payload_ref": reference,
        "observed_at": observed.isoformat() if observed else None,
        "collected_at": now.isoformat(),
        "freshness": "fresh"
        if observed and status in {"available", "empty"}
        else "stale"
        if status == "stale"
        else "unknown",
        "coverage_partial": coverage_partial or observed is None or future_observation or invalid_observation,
        "conflict": "conflicting" if status == "conflicting" else "none",
        "query_receipt": query_receipt or {},
    }
    raw = {
        "assessment_id": assessment["id"],
        "run_id": assessment.get("run_id"),
        "company_id": assessment["company_id"],
        "dimension": dimension,
        "raw_payload_ref": reference,
        "content_hash": digest,
        "raw_payload": payload,
        "collected_at": now,
        "staging_owner": str(assessment["collector_token"]),
    }
    return entry, raw


def collect_inputs(
    assessment: dict, profile: Any, *, include_soft: bool = True
) -> tuple[dict, list[dict], list[dict]]:
    from app.domains.risk import service
    from app.domains.risk.soft_risk import score_soft_risks

    name = profile.company_name
    policy = assessment["policy_snapshot"]
    required = policy["required_dimensions"]
    risk = service.get_risk_info(name) or RiskInfo(
        lawsuit_count=0,
        executed_count=0,
        abnormal_operation_count=0,
        administrative_penalty_count=0,
    )
    indicators = service.get_risk_indicators(name)
    financial = service.get_financial_metrics(name) if "financial" in required else None
    recent = service.get_recent_lawsuits(name, years=3) if "judicial" in required else 0
    extra_keys = (
        "dishonesty_count",
        "court_announcement_count",
        "consumption_restriction_count",
        "major_lawsuit",
        "legal_person_change_frequent",
        "net_profit_declining",
        "revenue_declining",
        "guarantee_count",
        "pledge_count",
        "bankruptcy_count",
        "env_penalty_count",
    )
    request = RiskCalculateRequest(
        company=profile,
        risk=risk,
        financial=financial,
        **{key: indicators[key] for key in extra_keys if key in indicators},
    )
    documents = {}
    db = get_db()
    for source in (
        *JUDICIAL_SOURCES,
        *OPERATIONAL_SOURCES,
        "riskInfo",
        "lawSuit_detail",
        "financial_cache",
    ):
        documents[source] = db[source].find_one({"name": name})
    context = {
        "risk": risk.model_dump(mode="json"),
        "indicators": indicators,
        "financial": financial.model_dump(mode="json") if financial else None,
        "policy_version": policy["version"],
    }
    soft = (
        score_soft_risks(name, context=_safe(context))
        if include_soft and "soft" in required
        else {"status": "not_requested", "dimensions": {}}
    )
    inputs = {
        "industry_category": service._classify_industry(profile.industry),
        "request": request.model_dump(mode="json"),
        "recent_lawsuits": recent,
        "soft": soft,
        "risk_detail": {
            **risk.model_dump(mode="json"),
            **{key: indicators.get(key) for key in extra_keys},
            "recent_lawsuits": recent,
        },
    }
    manifest, raw_payloads = [], []
    for dimension in required:
        observed = None
        partial = False
        receipt = {"status": "unavailable"}
        if dimension in {"judicial", "operational"}:
            sources = (
                JUDICIAL_SOURCES if dimension == "judicial" else OPERATIONAL_SOURCES
            )
            receipts = {
                source: provider_receipt(documents[source]) for source in sources
            }
            valid = [item for item in receipts.values() if item["status"] == "success"]
            count = sum(item["record_count"] for item in valid)
            # Positive summary counts are findings, but do not prove all sources were queried.
            known = (
                (
                    risk.lawsuit_count
                    + risk.executed_count
                    + int(indicators.get("dishonesty_count") or 0)
                )
                if dimension == "judicial"
                else (
                    risk.abnormal_operation_count
                    + risk.administrative_penalty_count
                    + int(indicators.get("bankruptcy_count") or 0)
                    + int(indicators.get("env_penalty_count") or 0)
                )
            )
            status = (
                "available"
                if count > 0 or known > 0
                else "empty"
                if len(valid) == len(sources)
                else "unavailable"
            )
            partial = len(valid) != len(sources)
            times = [
                _timestamp(
                    (documents[source] or {}).get("fetched_at")
                    or (documents[source] or {}).get("updated_at")
                )
                for source in sources
            ]
            known_times = [stamp for stamp in times if stamp is not None]
            observed = min(known_times) if known_times else None
            partial = partial or len(known_times) != len(times)
            receipt = {
                "status": "success" if valid else "unavailable",
                "record_count": count,
                "sources": receipts,
            }
            payload = {
                "documents": {
                    source: documents[source] for source in (*sources, "riskInfo")
                },
                "counts": _safe(context["risk"]),
                "indicators": indicators,
                "recent_lawsuits": recent if dimension == "judicial" else None,
            }
        elif dimension == "financial":
            status = "available" if financial else "unavailable"
            observed = (documents["financial_cache"] or {}).get("updated_at") or (
                documents["financial_cache"] or {}
            ).get("cached_at")
            payload = {
                "metrics": context["financial"],
                "source": documents["financial_cache"],
            }
            receipt = {
                "status": "success" if financial else "unavailable",
                "record_count": 1 if financial else None,
            }
        else:
            status = "available" if soft.get("status") == "available" else "unavailable"
            observed = soft.get("scored_at")
            payload = {"inference": soft, "context": _safe(context)}
            receipt = {
                "status": "success" if status == "available" else "unavailable",
                "record_count": len(soft.get("dimensions") or {}),
            }
        entry, raw = make_evidence(
            assessment,
            dimension,
            payload,
            status=status,
            source="soft_risk_model"
            if dimension == "soft"
            else "company_snapshot_read",
            policy=policy,
            observed_at=observed,
            query_receipt=receipt,
            coverage_partial=partial,
        )
        manifest.append(entry)
        raw_payloads.append(raw)
    return json_snapshot(inputs), manifest, raw_payloads
