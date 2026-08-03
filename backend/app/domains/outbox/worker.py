"""Synchronous interval entry point for one Transactional Outbox batch."""

import os
import socket
import uuid

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import (
    OUTBOX_OLDEST_PENDING_AGE_SECONDS,
    OUTBOX_PENDING_EVENTS,
    record_outbox_outcome,
)
from app.domains.outbox import repo
from app.domains.outbox.service import process_outbox_batch

logger = get_logger(__name__)


def run_outbox_once() -> dict:
    """Process one configured batch and refresh backlog metrics for an interval tick."""
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    result = {"claimed": 0, "published": 0, "failed": 0}
    status = "ok"

    try:
        result = process_outbox_batch(
            worker_id=worker_id,
            batch_size=settings.OUTBOX_BATCH_SIZE,
            max_attempts=settings.OUTBOX_MAX_ATTEMPTS,
            lease_seconds=settings.OUTBOX_LEASE_SECONDS,
            outcome_observer=record_outbox_outcome,
        )
    except Exception:
        status = "failed"
        logger.exception("outbox_worker_batch_failed", worker_id=worker_id)

    try:
        _refresh_pending_metrics()
    except Exception:
        status = "failed"
        logger.exception("outbox_worker_metrics_refresh_failed", worker_id=worker_id)

    return {**result, "worker_id": worker_id, "status": status}


def _refresh_pending_metrics() -> None:
    stats = repo.get_pending_stats()
    OUTBOX_PENDING_EVENTS.set(int(stats["pending"]))
    oldest_age_seconds = stats["oldest_age_seconds"]
    OUTBOX_OLDEST_PENDING_AGE_SECONDS.set(
        float(oldest_age_seconds) if oldest_age_seconds is not None else 0
    )
