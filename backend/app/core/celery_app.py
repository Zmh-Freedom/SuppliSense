"""
Celery application configuration.
"""

import os
from celery import Celery

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

celery_app = Celery(
    "supplier_risk",
    broker=f"{REDIS_URL}/0",
    backend=f"{REDIS_URL}/1",
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,  # 10 minutes hard limit
    task_soft_time_limit=480,  # 8 minutes soft limit
    worker_max_tasks_per_child=50,  # Prevent memory leaks
    worker_prefetch_multiplier=1,  # Fair task distribution
    task_acks_late=True,  # Acknowledge after task completion
    result_expires=3600,  # Results expire after 1 hour
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["app.tasks"])
