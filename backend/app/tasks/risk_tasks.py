"""
Risk assessment background tasks (plain functions, no Celery).
"""

import logging

logger = logging.getLogger(__name__)


def assess_risk_async(company_name: str) -> dict:
    """Run risk assessment in background."""
    from app.schemas import RiskAssessRequest
    from app.domains.risk.service import assess_risk

    try:
        result = assess_risk(RiskAssessRequest(company_name=company_name))
        return {
            "status": "success",
            "company_name": company_name,
            "result": result.model_dump(),
        }
    except Exception as e:
        logger.error("assess_risk_async_failed company=%s error=%s", company_name, e)
        return {
            "status": "error",
            "company_name": company_name,
            "error": str(e),
        }


def refresh_company_async(
    company_name: str,
    *,
    monitor_target_id: str | None = None,
) -> dict:
    """Run Tianyancha refresh in background (paid)."""
    from app.domains.alert.service import detect_changes
    from app.services.tianyancha_client import fetch_company

    try:
        fetch_company(company_name)
        changes = detect_changes(company_name, monitor_target_id=monitor_target_id)

        return {
            "status": "success",
            "company_name": company_name,
            "monitor_target_id": monitor_target_id,
            "changes": changes,
        }
    except Exception as e:
        logger.error("refresh_company_async_failed company=%s error=%s", company_name, e)
        return {
            "status": "error",
            "company_name": company_name,
            "error": str(e),
        }


def batch_refresh_all() -> dict:
    """Batch refresh all watched companies (paid)."""
    from app.domains.alert.service import get_watchlist_targets

    targets = get_watchlist_targets()
    results = []
    for target in targets:
        results.append(refresh_company_async(
            target.get("company_name", ""),
            monitor_target_id=target.get("monitor_target_id"),
        ))

    return {
        "status": "completed",
        "total": len(targets),
        "results": results,
    }


def check_all_async() -> dict:
    """Run financial check for all watched companies (free)."""
    from app.services.scheduler import run_financial_check

    try:
        result = run_financial_check()
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error("check_all_async_failed error=%s", e)
        return {"status": "error", "error": str(e)}
