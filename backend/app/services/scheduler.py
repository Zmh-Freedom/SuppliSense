import logging
import os

logging.basicConfig(level=logging.INFO)

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import settings
from app.domains.risk.repo_company import get_baseinfo
from app.domains.risk.repo_financial import get_financial_metrics
from app.domains.outbox.worker import run_outbox_once
from app.services.scheduler_leadership import SchedulerLeadership
from app.domains.alert.service import (
    detect_changes,
    get_latest_snapshot,
    get_watchlist_targets,
)

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler()
_scheduler_leadership = SchedulerLeadership()


def run_financial_check() -> dict:
    """Free: only check AkShare financial data, no Tianyancha API."""
    targets = get_watchlist_targets()
    if not targets:
        return {"checked": 0, "alerts": []}

    results = []
    for target in targets:
        name = target.get("company_name", "")
        monitor_target_id = target.get("monitor_target_id")
        try:
            profile = get_baseinfo(name)
            if not profile or not profile.is_listed:
                results.append({"company": name, "changed": False, "reason": "非上市"})
                continue

            financial = get_financial_metrics(name)
            if financial is None:
                results.append(
                    {"company": name, "changed": False, "reason": "无财报数据"}
                )
                continue

            prev = get_latest_snapshot(name, monitor_target_id=monitor_target_id)
            prev_fin = (prev.get("financial") or {}) if prev else {}
            from app.db.mongo import get_db
            from app.domains.risk.assessment_contracts import canonical_hash
            from datetime import datetime, timezone

            values = financial.model_dump(mode="json")
            collection = get_db()["financial_evidence_snapshots"]
            key = {
                "monitor_target_id": monitor_target_id,
                "company_id": target.get("company_id"),
            }
            previous = collection.find_one(key, sort=[("collected_at", -1)])
            previous_values = (previous or {}).get("financial") or prev_fin
            changes_list = [
                {
                    "field": field,
                    "old": previous_values.get(field),
                    "new": values.get(field),
                }
                for field in (
                    "revenue_growth",
                    "net_profit_growth",
                    "debt_ratio",
                    "cash_flow",
                )
                if previous_values.get(field) != values.get(field)
            ]
            digest = canonical_hash(values)
            evidence_id = canonical_hash(
                {
                    **key,
                    "content_hash": digest,
                    "previous_evidence_id": (previous or {}).get("_id"),
                }
            )
            if changes_list or not previous:
                collection.update_one(
                    {"_id": evidence_id},
                    {
                        "$setOnInsert": {
                            **key,
                            "company_name": name,
                            "financial": values,
                            "content_hash": digest,
                            "collected_at": datetime.now(timezone.utc),
                            "source": "financial_refresh",
                            "previous_evidence_id": (previous or {}).get("_id"),
                            "previous_assessment_id": (prev or {}).get("assessment_id"),
                        }
                    },
                    upsert=True,
                )
            if changes_list:
                from app.domains.alert.rules import get_rules, evaluate_changes

                labels = {
                    "revenue_growth": "营收增长",
                    "net_profit_growth": "净利润",
                    "debt_ratio": "资产负债率",
                    "cash_flow": "现金流",
                }
                rule_changes = [
                    {**item, "field": labels[item["field"]]} for item in changes_list
                ]
                triggered = evaluate_changes(get_rules(name), rule_changes)
                if triggered:
                    severity = (
                        "critical"
                        if any(item["severity"] == "critical" for item in triggered)
                        else "warning"
                    )
                    alert_id = "financial:" + evidence_id
                    created = get_db()["alerts"].update_one(
                        {"_id": alert_id},
                        {
                            "$setOnInsert": {
                                **key,
                                "company_name": name,
                                "supplier_id": target.get("supplier_id"),
                                "target_type": target.get("target_type"),
                                "financial_evidence_id": evidence_id,
                                "created_at": datetime.now(timezone.utc),
                                "changes": triggered,
                                "severity": severity,
                                "read": False,
                            }
                        },
                        upsert=True,
                    )
                    if created.upserted_id is not None:
                        from app.domains.alert.notifier import notify_target_change

                        notify_target_change(target, triggered, severity)
            changes = {"changed": bool(changes_list), "changes": changes_list}
            if changes["changed"]:
                logger.warning("Financial alert for %s: %s", name, changes["changes"])
            results.append(
                {
                    "company": name,
                    "changed": changes["changed"],
                    "changes": changes["changes"],
                }
            )
        except Exception as e:
            logger.error("Financial check failed for %s: %s", name, e)
            results.append({"company": name, "error": str(e)})

    return {"checked": len(targets), "channel": "free", "alerts": results}


def run_refresh_all() -> dict:
    """Paid: call Tianyancha API for all watched companies."""
    from app.services.tianyancha_client import fetch_company
    from app.schemas import RiskAssessRequest
    from app.domains.risk.service import assess_risk

    targets = get_watchlist_targets()
    if not targets:
        return {"checked": 0, "alerts": []}

    results = []
    for target in targets:
        name = target.get("company_name", "")
        monitor_target_id = target.get("monitor_target_id")
        try:
            fetch_company(name)
            from app.domains.risk.assessment_contracts import assessment_scope

            with assessment_scope(
                actor_id=target.get("owner_user_id") or "system:scheduler",
                monitor_target_id=monitor_target_id,
                company_id=target.get("company_id"),
                source="scheduler",
            ):
                assess_risk(RiskAssessRequest(company_name=name))
            changes = detect_changes(name, monitor_target_id=monitor_target_id)
            if changes["changed"]:
                logger.warning(
                    "Full refresh alert for %s: %s", name, changes["changes"]
                )
            results.append(
                {
                    "company": name,
                    "changed": changes["changed"],
                    "changes": changes["changes"],
                }
            )
        except Exception as e:
            logger.error("Full refresh failed for %s: %s", name, e)
            results.append({"company": name, "error": str(e)})

    return {"checked": len(targets), "channel": "paid", "alerts": results}


def _scheduled_financial() -> None:
    run_financial_check()


def _scheduled_refresh() -> None:
    run_refresh_all()


def _scheduled_digest() -> None:
    from app.services.feishu import send_daily_digest

    send_daily_digest()


def _scheduled_notify() -> None:
    from app.domains.alert.notifier import check_and_notify

    check_and_notify()


def _scheduled_sentiment() -> None:
    from app.domains.risk.sentiment import analyze_all_sentiment

    analyze_all_sentiment()


def _scheduled_proactive() -> None:
    from app.services.proactive_agent import run_proactive_analysis

    run_proactive_analysis()


def _scheduled_outbox() -> None:
    run_outbox_once()


def _scheduled_feishu_supplier_sync() -> None:
    try:
        from app.services.feishu_bitable import sync_supplier_tables
        from app.services.feishu_supplier_responsibility import (
            sync_supplier_responsibilities,
        )

        result = sync_supplier_tables()
        if result.get("tables", {}).get("supplier_master", {}).get("status") == "ok":
            sync_supplier_responsibilities()
    except Exception:
        logger.exception("feishu_supplier_sync_failed")


def start_scheduler() -> None:
    if _scheduler.running:
        return
    if not _scheduler_leadership.acquire():
        logger.info("scheduler_leadership_not_acquired")
        return
    free_cron = os.getenv("ALERT_CHECK_CRON", "0 9 * * *")
    paid_cron = os.getenv("ALERT_REFRESH_CRON", "0 9 * * 1")
    digest_cron = os.getenv("FEISHU_DIGEST_CRON", "0 9 * * *")
    sentiment_cron = os.getenv("SENTIMENT_CHECK_CRON", "0 10 * * *")
    notify_cron = os.getenv("ALERT_NOTIFY_CRON", "*/30 * * * *")
    proactive_cron = os.getenv("PROACTIVE_AGENT_CRON", "0 */2 * * *")

    _add_job(_scheduled_financial, free_cron, "financial_check")
    _add_job(_scheduled_digest, digest_cron, "daily_digest")
    _add_job(_scheduled_refresh, paid_cron, "full_refresh")
    _add_job(_scheduled_sentiment, sentiment_cron, "sentiment_check")
    _add_job(_scheduled_notify, notify_cron, "alert_notify")
    _add_job(_scheduled_proactive, proactive_cron, "proactive_agent")
    if settings.FEISHU_BITABLE_ENABLED and not settings.DEMO_DATA_FREEZE:
        _add_job(
            _scheduled_feishu_supplier_sync,
            settings.FEISHU_SUPPLIER_SYNC_CRON,
            "feishu_supplier_sync",
        )
    _add_outbox_job()

    try:
        _scheduler.start()
    except Exception:
        _scheduler_leadership.release()
        raise
    logger.info(
        "Scheduler started: financial[%s] digest[%s] refresh[%s] sentiment[%s] notify[%s] proactive[%s]",
        free_cron,
        digest_cron,
        paid_cron,
        sentiment_cron,
        notify_cron,
        proactive_cron,
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
        replace_existing=True,
    )


def _add_outbox_job() -> None:
    _scheduler.add_job(
        _scheduled_outbox,
        "interval",
        seconds=settings.OUTBOX_POLL_SECONDS,
        id="outbox_worker",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )


def stop_scheduler() -> None:
    try:
        if _scheduler.running:
            _scheduler.shutdown(wait=True)
            logger.info("Alert scheduler stopped (waited for running tasks)")
    finally:
        _scheduler_leadership.release()
