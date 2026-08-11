"""
Prometheus metrics for monitoring.
"""

from collections.abc import Callable
import os
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client import multiprocess


def _registered_metric(name: str, factory: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Return an existing collector when this module is reloaded in-process."""
    existing = REGISTRY._names_to_collectors.get(name)
    if existing is not None:
        return existing
    return factory(name, *args, **kwargs)

# HTTP request metrics
HTTP_REQUESTS_TOTAL = _registered_metric(
    "http_requests_total",
    Counter,
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

HTTP_REQUEST_DURATION = _registered_metric(
    "http_request_duration_seconds",
    Histogram,
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# Business metrics
RISK_ASSESSMENTS_TOTAL = _registered_metric(
    "risk_assessments_total",
    Counter,
    "Total risk assessments performed",
    ["company_name", "risk_level"],
)

SENTIMENT_ANALYSES_TOTAL = _registered_metric(
    "sentiment_analyses_total",
    Counter,
    "Total sentiment analyses performed",
    ["company_name", "has_data"],
)

ALERTS_TRIGGERED_TOTAL = _registered_metric(
    "alerts_triggered_total",
    Counter,
    "Total alerts triggered",
    ["severity", "type"],
)

# System metrics
ACTIVE_USERS = _registered_metric(
    "active_users",
    Gauge,
    "Number of active users",
)

TASKS_IN_QUEUE = _registered_metric(
    "tasks_in_queue",
    Gauge,
    "Number of tasks in Celery queue",
    ["queue"],
)

MONGO_DB_CONNECTIONS = _registered_metric(
    "mongo_db_connections",
    Gauge,
    "Number of MongoDB connections",
)

# Cache metrics
CACHE_HITS_TOTAL = _registered_metric(
    "cache_hits_total",
    Counter,
    "Total cache hits",
    ["cache_type"],
)

CACHE_MISSES_TOTAL = _registered_metric(
    "cache_misses_total",
    Counter,
    "Total cache misses",
    ["cache_type"],
)

# LLM metrics
LLM_CALLS_TOTAL = _registered_metric(
    "llm_calls_total",
    Counter,
    "Total LLM API calls",
    ["model", "status"],
)

LLM_CALL_DURATION = _registered_metric(
    "llm_call_duration_seconds",
    Histogram,
    "LLM API call duration in seconds",
    ["model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# Transactional Outbox metrics. Labels are intentionally constrained to known
# domain event and outcome values so they stay safe for Prometheus cardinality.
OUTBOX_PENDING_EVENTS = _registered_metric(
    "outbox_pending_events",
    Gauge,
    "Number of unpublished, non-dead-lettered Outbox events",
)
OUTBOX_OLDEST_PENDING_AGE_SECONDS = _registered_metric(
    "outbox_oldest_pending_age_seconds",
    Gauge,
    "Age in seconds of the oldest pending Outbox event",
)
OUTBOX_EVENTS_PROCESSED_TOTAL = _registered_metric(
    "outbox_events_processed_total",
    Counter,
    "Total terminally processed Outbox events",
    ["event_type", "status"],
)
OUTBOX_RETRIES_TOTAL = _registered_metric(
    "outbox_retries_total",
    Counter,
    "Total Outbox event retries scheduled",
    ["event_type"],
)
COMPANY_IDENTITY_RESOLUTIONS_TOTAL = _registered_metric(
    "company_identity_resolutions_total",
    Counter,
    "Total company identity resolutions by deterministic outcome",
    ["resolution"],
)

_OUTBOX_EVENT_TYPES = frozenset({
    "company.created",
    "company.updated",
    "company.verified",
    "company.merged",
})
_OUTBOX_TERMINAL_STATUSES = frozenset({"published", "dead_lettered"})
_IDENTITY_RESOLUTIONS = frozenset({
    "exact",
    "candidates",
    "pending_verification",
})


def record_outbox_outcome(event_type: str, outcome: str) -> None:
    """Record one mutually exclusive delivery outcome for an Outbox event."""
    event_label = event_type if event_type in _OUTBOX_EVENT_TYPES else "other"
    if outcome == "retry":
        OUTBOX_RETRIES_TOTAL.labels(event_type=event_label).inc()
        return
    if outcome in _OUTBOX_TERMINAL_STATUSES:
        OUTBOX_EVENTS_PROCESSED_TOTAL.labels(
            event_type=event_label,
            status=outcome,
        ).inc()


def record_company_identity_resolution(resolution: str) -> None:
    """Record a resolution outcome without using the user query as a label."""
    label = resolution if resolution in _IDENTITY_RESOLUTIONS else "other"
    COMPANY_IDENTITY_RESOLUTIONS_TOTAL.labels(resolution=label).inc()


def get_metrics() -> tuple[str, str]:
    """Get Prometheus metrics in text format."""
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry).decode("utf-8"), CONTENT_TYPE_LATEST
    return generate_latest().decode("utf-8"), CONTENT_TYPE_LATEST
