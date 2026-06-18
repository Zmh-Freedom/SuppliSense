import logging
import os

logging.basicConfig(level=logging.INFO)

from apscheduler.schedulers.background import BackgroundScheduler

from app.repositories.company_repo import get_baseinfo
from app.repositories.financial_repo import get_financial_metrics
from app.services.alert_service import (
    detect_changes,
    get_latest_snapshot,
    get_watchlist,
    save_snapshot,
)

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler()


def run_financial_check() -> dict:
    """Free: only check AkShare financial data, no Tianyancha API."""
    companies = get_watchlist()
    if not companies:
        return {"checked": 0, "alerts": []}

    results = []
    for name in companies:
        try:
            profile = get_baseinfo(name)
            if not profile or not profile.is_listed:
                results.append({"company": name, "changed": False, "reason": "非上市"})
                continue

            financial = get_financial_metrics(name)
            if financial is None:
                results.append({"company": name, "changed": False, "reason": "无财报数据"})
                continue

            prev = get_latest_snapshot(name)
            prev_fin = (prev.get("financial") or {}) if prev else {}
            changed = (
                prev_fin.get("revenue_growth") != financial.revenue_growth
                or prev_fin.get("net_profit_growth") != financial.net_profit_growth
                or prev_fin.get("debt_ratio") != financial.debt_ratio
                or prev_fin.get("cash_flow") != financial.cash_flow
            )

            if changed or not prev:
                # Save updated snapshot with new financial data
                from app.schemas import RiskCalculateResponse

                old_detail = (prev.get("risk_detail") or {}) if prev else {}
                response = RiskCalculateResponse(
                    risk_score=prev.get("risk_score", 0) if prev else 0,
                    risk_level=prev.get("risk_level", "未知") if prev else "未知",
                    financial=financial,
                    risk_detail=old_detail,
                )
                save_snapshot(name, response)

            changes = detect_changes(name)
            if changes["changed"]:
                logger.warning("Financial alert for %s: %s", name, changes["changes"])
            results.append({
                "company": name,
                "changed": changes["changed"],
                "changes": changes["changes"],
            })
        except Exception as e:
            logger.error("Financial check failed for %s: %s", name, e)
            results.append({"company": name, "error": str(e)})

    return {"checked": len(companies), "channel": "free", "alerts": results}


def run_refresh_all() -> dict:
    """Paid: call Tianyancha API for all watched companies."""
    from app.services.tianyancha_client import fetch_company
    from app.schemas import RiskAssessRequest
    from app.services.risk_service import assess_risk

    companies = get_watchlist()
    if not companies:
        return {"checked": 0, "alerts": []}

    results = []
    for name in companies:
        try:
            fetch_company(name)
            assess_risk(RiskAssessRequest(company_name=name))
            changes = detect_changes(name)
            if changes["changed"]:
                logger.warning("Full refresh alert for %s: %s", name, changes["changes"])
            results.append({
                "company": name,
                "changed": changes["changed"],
                "changes": changes["changes"],
            })
        except Exception as e:
            logger.error("Full refresh failed for %s: %s", name, e)
            results.append({"company": name, "error": str(e)})

    return {"checked": len(companies), "channel": "paid", "alerts": results}


def _scheduled_financial() -> None:
    run_financial_check()


def _scheduled_refresh() -> None:
    run_refresh_all()


def _scheduled_digest() -> None:
    from app.services.feishu import send_daily_digest
    send_daily_digest()


def _scheduled_sentiment() -> None:
    from app.services.sentiment import analyze_all_sentiment
    analyze_all_sentiment()


def start_scheduler() -> None:
    free_cron = os.getenv("ALERT_CHECK_CRON", "0 9 * * *")
    paid_cron = os.getenv("ALERT_REFRESH_CRON", "0 9 * * 1")
    digest_cron = os.getenv("FEISHU_DIGEST_CRON", "0 9 * * *")
    sentiment_cron = os.getenv("SENTIMENT_CHECK_CRON", "0 10 * * *")

    _add_job(_scheduled_financial, free_cron, "financial_check")
    _add_job(_scheduled_digest, digest_cron, "daily_digest")
    _add_job(_scheduled_refresh, paid_cron, "full_refresh")
    _add_job(_scheduled_sentiment, sentiment_cron, "sentiment_check")

    _scheduler.start()
    logger.info(
        "Scheduler started: financial[%s] digest[%s] refresh[%s] sentiment[%s]",
        free_cron, digest_cron, paid_cron, sentiment_cron,
    )


def _add_job(func, cron: str, job_id: str) -> None:
    parts = cron.strip().split()
    if len(parts) != 5:
        logger.warning("Invalid cron for %s: %s", job_id, cron)
        return
    _scheduler.add_job(
        func,
        "cron",
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4],
        id=job_id,
    )


def stop_scheduler() -> None:
    _scheduler.shutdown(wait=True)
    logger.info("Alert scheduler stopped (waited for running tasks)")
