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


def run_financial_check_for_target(target: dict) -> dict:
    """Free: check AkShare financial data for one monitoring target."""
    name = target.get("company_name", "")
    monitor_target_id = target.get("monitor_target_id")
    profile = get_baseinfo(name)
    if not profile or not profile.is_listed:
        return {"company": name, "changed": False, "reason": "非上市"}

    financial = get_financial_metrics(name)
    if financial is None:
        return {"company": name, "changed": False, "reason": "无财报数据"}

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
    return {
        "company": name,
        "changed": changes["changed"],
        "changes": changes["changes"],
    }


def run_financial_check() -> dict:
    """Free: only check AkShare financial data, no Tianyancha API."""
    targets = get_watchlist_targets()
    if not targets:
        return {"checked": 0, "alerts": []}

    results = []
    for target in targets:
        name = target.get("company_name", "")
        try:
            results.append(run_financial_check_for_target(target))
        except Exception as e:
            logger.error("Financial check failed for %s: %s", name, e)
            results.append({"company": name, "error": str(e)})

    return {"checked": len(targets), "channel": "free", "alerts": results}


def run_refresh_for_target(target: dict, actor_id: str) -> dict:
    """Paid: refresh one target through Tianyancha and reassess it."""
    from app.services.tianyancha_client import fetch_company
    from app.schemas import RiskAssessRequest
    from app.domains.risk.service import assess_risk
    from app.domains.risk.assessment_contracts import assessment_scope

    name = target.get("company_name", "")
    monitor_target_id = target.get("monitor_target_id")
    if not str(actor_id or "").strip():
        raise ValueError("付费刷新缺少明确操作者")
    fetch_company(name)
    with assessment_scope(
        actor_id=actor_id,
        monitor_target_id=monitor_target_id,
        company_id=target.get("company_id"),
        source="scheduler",
    ):
        assess_risk(RiskAssessRequest(company_name=name))
    changes = detect_changes(name, monitor_target_id=monitor_target_id)
    if changes["changed"]:
        logger.warning("Full refresh alert for %s: %s", name, changes["changes"])
    return {
        "company": name,
        "changed": changes["changed"],
        "changes": changes["changes"],
    }


def run_refresh_all(
    *, targets: list[dict] | None = None, actor_id: str | None = None
) -> dict:
    """Paid: call Tianyancha API for all watched companies."""
    targets = get_watchlist_targets() if targets is None else targets
    if not targets:
        return {"checked": 0, "alerts": []}

    results = []
    for target in targets:
        name = target.get("company_name", "")
        try:
            results.append(
                run_refresh_for_target(
                    target,
                    actor_id or target.get("owner_user_id") or "system:scheduler",
                )
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


def _add_business_jobs() -> None:
    free_cron = os.getenv("ALERT_CHECK_CRON", "0 9 * * *")
    paid_cron = os.getenv("ALERT_REFRESH_CRON", "0 9 * * 1")
    digest_cron = os.getenv("FEISHU_DIGEST_CRON", "0 9 * * *")
    sentiment_cron = os.getenv("SENTIMENT_CHECK_CRON", "0 10 * * *")
    notify_cron = os.getenv("ALERT_NOTIFY_CRON", "*/30 * * * *")
    proactive_cron = os.getenv("PROACTIVE_AGENT_CRON", "0 */2 * * *")

    migrated = {
        item.strip() for item in settings.SCHEDULER_MIGRATED_KINDS.split(",")
        if item.strip()
    }
    jobs = [
        (_scheduled_financial, free_cron, "financial_check"),
        (_scheduled_digest, digest_cron, "daily_digest"),
        (_scheduled_refresh, paid_cron, "full_refresh"),
        (_scheduled_sentiment, sentiment_cron, "sentiment_check"),
        (_scheduled_notify, notify_cron, "alert_notify"),
        (_scheduled_proactive, proactive_cron, "proactive_agent"),
    ]
    for func, cron, job_id in jobs:
        if job_id not in migrated:
            _add_job(func, cron, job_id)
    if (settings.FEISHU_BITABLE_ENABLED and not settings.DEMO_DATA_FREEZE
            and "feishu_supplier_sync" not in migrated):
        _add_job(
            _scheduled_feishu_supplier_sync,
            settings.FEISHU_SUPPLIER_SYNC_CRON,
            "feishu_supplier_sync",
        )
    logger.info(
        "Business scheduler started: financial[%s] digest[%s] refresh[%s] "
        "sentiment[%s] notify[%s] proactive[%s]",
        free_cron, digest_cron, paid_cron, sentiment_cron, notify_cron, proactive_cron,
    )


def start_scheduler() -> None:
    """Start the roles this process owns.

    Advisory leadership still serializes Outbox polling until 3C moves it.
    SCHEDULER_ROLE_ENABLED only controls the legacy in-process business cron;
    when a dedicated scheduler entry is deployed the API sets it False.
    OUTBOX_WORKER_ENABLED remains the delivery rollback switch inside the job.
    """
    if _scheduler.running:
        return
    if not _scheduler_leadership.acquire():
        logger.info("scheduler_leadership_not_acquired")
        return
    if settings.SCHEDULER_ROLE_ENABLED:
        _add_business_jobs()
    _add_outbox_job()
    try:
        _scheduler.start()
    except Exception:
        _scheduler_leadership.release()
        raise
    logger.info(
        "Scheduler started: business_role=%s outbox=%s",
        settings.SCHEDULER_ROLE_ENABLED, settings.OUTBOX_WORKER_ENABLED,
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
