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

# Sourcing Risk Agent V2 metrics. Labels are finite domain values only; IDs,
# names, prompts and provider-specific identifiers must remain log attributes.
AGENT_RUNS_TOTAL = _registered_metric(
    "agent_runs_total",
    Counter,
    "Total Sourcing Risk Agent V2 runs",
    ["status", "rollout"],
)
AGENT_STAGE_DURATION_SECONDS = _registered_metric(
    "agent_stage_duration_seconds",
    Histogram,
    "Sourcing Risk Agent V2 stage duration",
    ["stage"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)
AGENT_PROVIDER_OUTCOMES_TOTAL = _registered_metric(
    "agent_provider_outcomes_total",
    Counter,
    "Sourcing Risk Agent V2 provider outcomes",
    ["provider", "outcome"],
)
AGENT_CANDIDATE_GROUPS_TOTAL = _registered_metric(
    "agent_candidate_groups_total",
    Counter,
    "Sourcing Risk Agent V2 candidate decision groups",
    ["group"],
)
AGENT_ACTION_OUTCOMES_TOTAL = _registered_metric(
    "agent_action_outcomes_total",
    Counter,
    "Sourcing Risk Agent V2 action outcomes",
    ["action", "outcome"],
)
AGENT_EVAL_CASES_TOTAL = _registered_metric(
    "agent_eval_cases_total",
    Counter,
    "Offline Sourcing Risk Agent V2 evaluation cases",
    ["eval_version", "capability", "outcome"],
)

_AGENT_STATUSES = frozenset({"created", "running", "completed", "clarification", "failed", "cancelled"})
_AGENT_ROLLOUTS = frozenset({"shadow", "internal", "canary", "default"})
_AGENT_STAGES = frozenset({
    "requirement_parsing", "local_discovery", "external_discovery", "identity",
    "evidence", "decision", "approval", "recovery",
})
_AGENT_PROVIDERS = frozenset({"local", "external", "financial", "sanctions", "judicial", "other"})
_AGENT_PROVIDER_OUTCOMES = frozenset({"success", "empty", "staged", "timeout", "unavailable", "error"})
_AGENT_CANDIDATE_GROUPS = frozenset({"recommended", "alternative", "needs_review", "rejected"})
_AGENT_ACTIONS = frozenset({"import", "watchlist", "access_application", "export", "none"})
_AGENT_ACTION_OUTCOMES = frozenset({"proposed", "approved", "rejected", "executed", "blocked", "failed"})
_AGENT_EVAL_OUTCOMES = frozenset({"pass", "fail"})


def record_agent_run(status: str, rollout: str = "default") -> None:
    """Record a V2 run using bounded status and rollout labels."""
    AGENT_RUNS_TOTAL.labels(
        status=status if status in _AGENT_STATUSES else "failed",
        rollout=rollout if rollout in _AGENT_ROLLOUTS else "default",
    ).inc()


def record_agent_stage(stage: str, duration_seconds: float) -> None:
    """Record a stage duration without exposing a run or company identifier."""
    AGENT_STAGE_DURATION_SECONDS.labels(
        stage=stage if stage in _AGENT_STAGES else "recovery",
    ).observe(max(0.0, duration_seconds))


def record_agent_provider(provider: str, outcome: str) -> None:
    """Record a provider result using a fixed provider/outcome vocabulary."""
    AGENT_PROVIDER_OUTCOMES_TOTAL.labels(
        provider=provider if provider in _AGENT_PROVIDERS else "other",
        outcome=outcome if outcome in _AGENT_PROVIDER_OUTCOMES else "error",
    ).inc()


def record_agent_candidate_group(group: str) -> None:
    """Record one bounded candidate decision group."""
    AGENT_CANDIDATE_GROUPS_TOTAL.labels(
        group=group if group in _AGENT_CANDIDATE_GROUPS else "needs_review",
    ).inc()


def record_agent_action(action: str, outcome: str) -> None:
    """Record an action proposal/execution outcome without target identifiers."""
    AGENT_ACTION_OUTCOMES_TOTAL.labels(
        action=action if action in _AGENT_ACTIONS else "none",
        outcome=outcome if outcome in _AGENT_ACTION_OUTCOMES else "blocked",
    ).inc()


def record_agent_eval(capability: str, outcome: str, eval_version: str = "v1") -> None:
    """Record an offline Eval case with bounded capability and result labels."""
    AGENT_EVAL_CASES_TOTAL.labels(
        eval_version=eval_version if eval_version in {"v1"} else "v1",
        capability=capability if capability in {
            "requirement_parsing", "local_first_discovery", "identity_evidence_safety",
            "decision_action_boundary", "recovery_fail_closed",
        } else "other",
        outcome=outcome if outcome in _AGENT_EVAL_OUTCOMES else "fail",
    ).inc()

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
