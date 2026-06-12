"""
Scheduled tasks (Celery Beat).
"""

from celery.schedules import crontab

from app.core.celery_app import celery_app
from app.tasks.risk_tasks import batch_refresh_all, check_all_async
from app.tasks.sentiment_tasks import analyze_all_sentiment_async

# Configure periodic tasks
celery_app.conf.beat_schedule = {
    "daily-financial-check": {
        "task": "check_all_async",
        "schedule": crontab(hour=9, minute=0),  # Every day at 9:00
    },
    "weekly-full-refresh": {
        "task": "batch_refresh_all",
        "schedule": crontab(hour=9, minute=0, day_of_week=1),  # Every Monday at 9:00
    },
    "daily-sentiment-check": {
        "task": "analyze_all_sentiment_async",
        "schedule": crontab(hour=10, minute=0),  # Every day at 10:00
    },
    "daily-feishu-digest": {
        "task": "send_daily_digest_async",
        "schedule": crontab(hour=9, minute=5),  # Every day at 9:05
    },
}


@celery_app.task(bind=True, name="send_daily_digest_async")
def send_daily_digest_async(self) -> dict:
    """Send daily Feishu digest."""
    from app.services.feishu import send_daily_digest

    try:
        self.update_state(state="PROGRESS", meta={"status": "发送日报中"})
        send_daily_digest()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
