"""Supplier profile aggregation service.

Builds a unified supplier profile by querying all existing domains
(risk, financial, sentiment, compliance, ESG, alerts, relationships).
Each domain query is isolated — one failure does not affect others.
"""

from datetime import datetime, timedelta, timezone

from app.core.logging import get_logger

logger = get_logger()


def build_supplier_profile(supplier_id: str) -> dict:
    """Build the complete supplier profile from all domain sources.

    Args:
        supplier_id: The supplier _id (UUID string).

    Returns:
        Complete profile dict matching SupplierProfileResponse schema.

    Raises:
        ValueError: If supplier not found.
    """
    from app.domains.supplier.repo import get_supplier, get_changelog

    master = get_supplier(supplier_id)
    if not master:
        raise ValueError(f"Supplier {supplier_id} not found")

    name = master["name"]
    stable_supplier_id = str(master.get("supplier_id") or master.get("_id") or supplier_id)
    enrichment = _try_build("master_enrichment", name, _load_cached_enrichment, master) or {}

    # Build each section independently — failures are logged but don't block
    profile: dict = {
        "basic_info": _build_basic_info(master, enrichment),
        "risk": _try_build("risk", name, _build_risk_summary, name),
        "financial": _try_build("financial", name, _build_financial_snapshot, name, master),
        "sentiment": _try_build("sentiment", name, _build_sentiment_summary, name),
        "compliance": _try_build("compliance", name, _build_compliance_status, name),
        "esg": _try_build("esg", name, _build_esg_summary, name),
        "alerts": _try_build("alerts", name, _build_alert_list, name) or [],
        "relationships": _try_build("relationships", name, _build_relationship_summary, name),
        "changelog": _try_build("changelog", stable_supplier_id, get_changelog, stable_supplier_id, 20) or [],
    }

    return profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _try_build(section: str, key: str, fn, *args):
    """Call fn(*args) and return its result, logging errors silently."""
    try:
        return fn(*args)
    except Exception:
        logger.exception("profile_section_failed", section=section, key=key)
        return None


def _build_basic_info(master: dict, enrichment: dict | None = None) -> dict:
    """Build read-only master data view with source and freshness metadata.

    Cached Tianyancha and staged-candidate fields are display-only fallbacks.
    A profile read must never silently promote those values into supplier master
    data, because that is a business write requiring human confirmation.
    """
    enrichment = enrichment or {}
    master_source = str(master.get("source") or "manual")
    master_updated_at = _to_iso(master.get("updated_at") or master.get("created_at"))
    industry = master.get("industry") or enrichment.get("industry")
    categories = master.get("categories", [])
    if not industry and categories:
        industry = categories[0]

    website_url = master.get("website_url") or enrichment.get("website_url")
    contact_phone = master.get("contact_phone") or enrichment.get("contact_phone")
    contact_email = master.get("contact_email") or enrichment.get("contact_email")

    return {
        "name": master.get("name", ""),
        "unified_code": master.get("unified_code"),
        "legal_person": master.get("legal_person"),
        "registered_capital": master.get("registered_capital"),
        "establish_time": master.get("establish_time"),
        "reg_status": master.get("reg_status"),
        "industry": industry,
        "categories": categories,
        "regions": master.get("regions", []),
        "scale": master.get("scale"),
        "address": master.get("address"),
        "contact_person": master.get("contact_person"),
        "contact_phone": contact_phone,
        "contact_email": contact_email,
        "website_url": website_url,
        "source": master_source,
        "updated_at": master_updated_at,
        "industry_source": (
            master_source if master.get("industry") else enrichment.get("industry_source") or master_source
        ),
        "industry_updated_at": (
            master_updated_at if master.get("industry") else enrichment.get("industry_updated_at") or master_updated_at
        ),
        "website_url_source": (
            master_source if master.get("website_url") else enrichment.get("website_url_source")
        ),
        "contact_phone_source": (
            master_source if master.get("contact_phone") else enrichment.get("contact_phone_source")
        ),
        "contact_email_source": (
            master_source if master.get("contact_email") else enrichment.get("contact_email_source")
        ),
        "status": master.get("status", "prospective"),
    }


def _load_cached_enrichment(master: dict) -> dict:
    """Read cached public enrichment without network calls or master-data writes."""
    from app.db.mongo import get_db

    db = get_db()
    name = str(master.get("name") or "")
    if not name:
        return {}

    result: dict = {}
    candidate = db["external_supplier_candidates"].find_one(
        {"$or": [{"supplier_name": name}, {"tianyancha_company_name": name}]},
        sort=[("updated_at", -1)],
    )
    if candidate:
        _merge_enrichment_fields(
            result,
            candidate,
            default_source=str(candidate.get("source") or "staged_external"),
            updated_at=candidate.get("updated_at"),
        )

    baseinfo = db["baseinfo"].find_one({"name": name})
    parsed_baseinfo = _parse_baseinfo_result(baseinfo)
    if parsed_baseinfo:
        from app.domains.sourcing.supplier_repo import _resolve_category

        industry = _resolve_category(parsed_baseinfo)
        if industry and not result.get("industry"):
            result["industry"] = industry
            result["industry_source"] = "tianyancha_baseinfo_cache"
            result["industry_updated_at"] = _to_iso(
                baseinfo.get("updated_at") if baseinfo else None
            )
        _merge_enrichment_fields(
            result,
            {
                "website_url": _first_string(parsed_baseinfo, "website", "webSite", "websiteUrl", "webUrl"),
                "contact_phone": _first_string(parsed_baseinfo, "phone", "phoneNumber", "tel", "telephone"),
                "contact_email": _first_string(parsed_baseinfo, "email", "emailAddress", "mail"),
            },
            default_source="tianyancha_baseinfo_cache",
            updated_at=baseinfo.get("updated_at") if baseinfo else None,
        )
    return result


def _parse_baseinfo_result(baseinfo: dict | None) -> dict | None:
    """Extract a Tianyancha baseinfo result from either supported cache shape."""
    if not isinstance(baseinfo, dict):
        return None
    items = baseinfo.get("items")
    if isinstance(items, dict) and isinstance(items.get("result"), dict):
        return items["result"]
    result = baseinfo.get("result")
    return result if isinstance(result, dict) else None


def _merge_enrichment_fields(
    target: dict,
    values: dict,
    *,
    default_source: str,
    updated_at: object,
) -> None:
    """Copy missing contact fields while preserving per-field provenance."""
    for field in ("website_url", "contact_phone", "contact_email"):
        value = str(values.get(field) or "").strip()
        if value and not target.get(field):
            target[field] = value
            target[f"{field}_source"] = values.get(f"{field}_source") or default_source
    if values.get("industry") and not target.get("industry"):
        target["industry"] = values["industry"]
        target["industry_source"] = values.get("industry_source") or default_source
        target["industry_updated_at"] = _to_iso(updated_at)


def _first_string(values: dict, *keys: str) -> str | None:
    """Return the first non-empty string in a source payload."""
    for key in keys:
        value = str(values.get(key) or "").strip()
        if value:
            return value
    return None


def _to_iso(value: object) -> str | None:
    """Serialize optional timestamps consistently for API consumers."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


# ---- Risk ----

def _build_risk_summary(company_name: str) -> dict | None:
    """Build risk summary from assessment_history (PG) or alert_snapshots (Mongo)."""
    from app.domains.risk.service import SCORING_VERSION

    trend_data: list[dict] = []
    risk_score = 0
    risk_level = "未知"
    last_checked = None

    # Prefer PostgreSQL
    try:
        from app.domains.risk.repo_assessment import get_trend as pg_get_trend

        data = pg_get_trend(company_name, days=90, scoring_version=SCORING_VERSION)
        if data:
            trend_data = data
    except Exception:
        pass

    # Fallback to MongoDB alert_snapshots
    if not trend_data:
        from app.db.mongo import get_db

        db = get_db()
        since = datetime.now(timezone.utc) - timedelta(days=90)
        snapshots = list(
            db["alert_snapshots"]
            .find(
                {"company_name": company_name, "checked_at": {"$gte": since}},
                {"checked_at": 1, "risk_score": 1, "risk_level": 1, "_id": 0},
            )
            .sort("checked_at", 1)
        )
        trend_data = [
            {
                "date": s["checked_at"].strftime("%Y-%m-%d"),
                "risk_score": s.get("risk_score", 0),
                "risk_level": s.get("risk_level", ""),
            }
            for s in snapshots
        ]

    if trend_data:
        latest = trend_data[-1]
        risk_score = latest.get("risk_score", 0)
        risk_level = latest.get("risk_level", "未知")
        last_checked = latest.get("date", "")

    # Check watchlist
    in_watchlist = False
    alert_count = 0
    if trend_data:
        try:
            from app.db.mongo import get_db
            db = get_db()
            wl = db["watchlist"].find_one({"company_name": company_name})
            in_watchlist = wl is not None
            alert_count = db["alerts"].count_documents({"company_name": company_name})
        except Exception:
            pass

    return {
        "risk_score": risk_score,
        "risk_level": risk_level,
        "trend": trend_data,
        "alert_count": alert_count,
        "last_checked": last_checked,
        "in_watchlist": in_watchlist,
    }


# ---- Financial ----

def _build_financial_snapshot(company_name: str, master: dict) -> dict | None:
    """Build financial snapshot from financial_cache + master record."""
    from app.db.mongo import get_db

    db = get_db()
    cache = db["financial_cache"].find_one({"name": company_name})

    result: dict = {
        "revenue_growth": None,
        "net_profit_growth": None,
        "debt_ratio": None,
        "cash_flow": None,
        "roe": None,
        "net_profit_margin": None,
        "current_ratio": None,
        "quick_ratio": None,
        "credit_rating": master.get("credit_rating"),
        "annual_revenue": master.get("annual_revenue"),
        "cached_at": None,
        "history": [],
    }

    if cache:
        metrics = cache.get("metrics", {})
        result["revenue_growth"] = metrics.get("revenue_growth")
        result["net_profit_growth"] = metrics.get("net_profit_growth")
        result["debt_ratio"] = metrics.get("debt_ratio")
        result["cash_flow"] = metrics.get("cash_flow")
        result["roe"] = metrics.get("roe")
        result["net_profit_margin"] = metrics.get("net_profit_margin")
        result["current_ratio"] = metrics.get("current_ratio")
        result["quick_ratio"] = metrics.get("quick_ratio")
        cached_at = cache.get("cached_at")
        if cached_at and hasattr(cached_at, "isoformat"):
            result["cached_at"] = cached_at.isoformat()
        result["history"] = _normalise_financial_history(cache)

    return result


def _normalise_financial_history(cache: dict) -> list[dict]:
    """Normalize historical financial records from supported cache payloads."""
    raw_history = (
        cache.get("history")
        or cache.get("financial_history")
        or cache.get("metrics_history")
        or []
    )
    if not isinstance(raw_history, list):
        return []

    history: list[dict] = []
    for item in raw_history:
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else item
        period = item.get("period") or item.get("date") or item.get("year") or item.get("report_date")
        row = {
            "period": str(period) if period is not None else "",
            "revenue": metrics.get("revenue") or metrics.get("annual_revenue"),
            "net_profit": metrics.get("net_profit"),
            "debt_ratio": metrics.get("debt_ratio"),
            "cash_flow": metrics.get("cash_flow"),
        }
        if row["period"] and any(value is not None for key, value in row.items() if key != "period"):
            history.append(row)
    return history


# ---- Sentiment ----

def _build_sentiment_summary(company_name: str) -> dict | None:
    """Build sentiment summary from sentiment_results collection."""
    from app.db.mongo import get_db

    db = get_db()
    doc = db["sentiment_results"].find_one(
        {"company_name": company_name},
        sort=[("analyzed_at", -1)],
    )

    if not doc:
        return None

    analyzed_at = doc.get("analyzed_at")
    if analyzed_at and hasattr(analyzed_at, "isoformat"):
        analyzed_at = analyzed_at.isoformat()

    return {
        "overall_sentiment": doc.get("overall_sentiment", "未知"),
        "sentiment_score": doc.get("sentiment_score", 0),
        "negative_ratio": doc.get("negative_ratio", 0),
        "article_count": doc.get("article_count", 0),
        "top_tags": doc.get("top_tags", []),
        "analyzed_at": analyzed_at,
    }


# ---- Compliance ----

def _build_compliance_status(company_name: str) -> dict:
    """Build compliance status from risk collections."""
    from app.db.mongo import get_db

    db = get_db()

    # Count helper
    def _count(coll: str) -> int:
        doc = db[coll].find_one({"name": company_name})
        if not doc:
            return 0
        items = doc.get("items", {})
        if isinstance(items, dict):
            result = items.get("result")
            if isinstance(result, dict):
                return result.get("total", 0) or 0
        return 0

    # Sanctions
    sanctions_clean = True
    sanctions_count = 0
    try:
        from app.domains.risk.sanctions_service import assess_sanctions
        sanctions = assess_sanctions(company_name)
        if sanctions:
            sanctions_clean = sanctions.get("clean", True)
            sanctions_count = sanctions.get("match_count", 0)
    except Exception:
        pass

    return {
        "sanctions_clean": sanctions_clean,
        "sanctions_match_count": sanctions_count,
        "lawsuit_count": _count("lawSuit") + _count("courtRegister"),
        "executed_count": _count("executedPerson"),
        "dishonesty_count": _count("dishonesty"),
        "abnormal_operation_count": _count("abnormal"),
        "administrative_penalty_count": _count("punishmentInfo"),
        "tax_arrears_count": _count("taxArrears"),
    }


# ---- ESG ----

def _build_esg_summary(company_name: str) -> dict | None:
    """Build ESG summary by calling the existing ESG service."""
    try:
        from app.domains.risk.esg_service import assess_esg
        result = assess_esg(company_name)
        if result:
            return {
                "environmental": result.get("environmental"),
                "social": result.get("social"),
                "governance": result.get("governance"),
            }
    except Exception:
        pass
    return None


# ---- Alerts ----

def _build_alert_list(company_name: str) -> list[dict]:
    """Get recent alerts for a company."""
    from app.db.mongo import get_db

    db = get_db()
    docs = list(
        db["alerts"]
        .find({"company_name": company_name})
        .sort("created_at", -1)
        .limit(20)
    )
    result: list[dict] = []
    for doc in docs:
        created_at = doc.get("created_at")
        if created_at and hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        result.append({
            "_id": str(doc["_id"]),
            "company_name": company_name,
            "severity": doc.get("severity", "warning"),
            "changes": doc.get("changes", []),
            "created_at": str(created_at) if created_at else "",
        })
    return result


# ---- Relationships ----

def _build_relationship_summary(company_name: str) -> dict | None:
    """Build relationship summary via contagion analysis."""
    try:
        from app.domains.risk.contagion import analyze_contagion
        result = analyze_contagion(company_name)
        if result:
            entities = result.get("related_entities", [])
            normalized_entities = _attach_supplier_links(entities if isinstance(entities, list) else [])
            return {
                "related_count": result.get("related_count", 0),
                "branch_count": result.get("branch_count", 0),
                "dependency_count": result.get("dependency_count", 0),
                "high_risk_related_count": result.get("high_risk_related_count", 0),
                "entities": normalized_entities,
            }
    except Exception:
        pass
    return None


def _attach_supplier_links(entities: list[dict]) -> list[dict]:
    """Attach supplier ids to related entities when they exist in the local library."""
    from app.domains.supplier.repo import get_supplier_by_name

    normalized: list[dict] = []
    resolved: dict[str, str | None] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        item = dict(entity)
        name = str(item.get("name") or "").strip()
        if name:
            if name not in resolved:
                supplier = get_supplier_by_name(name)
                resolved[name] = str(supplier.get("_id")) if supplier else None
            if resolved[name]:
                item["supplier_id"] = resolved[name]
        normalized.append(item)
    return normalized
