"""
Prometheus metrics for monitoring.
"""

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# HTTP request metrics
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# Business metrics
RISK_ASSESSMENTS_TOTAL = Counter(
    "risk_assessments_total",
    "Total risk assessments performed",
    ["company_name", "risk_level"],
)

SENTIMENT_ANALYSES_TOTAL = Counter(
    "sentiment_analyses_total",
    "Total sentiment analyses performed",
    ["company_name", "has_data"],
)

ALERTS_TRIGGERED_TOTAL = Counter(
    "alerts_triggered_total",
    "Total alerts triggered",
    ["severity", "type"],
)

# System metrics
ACTIVE_USERS = Gauge(
    "active_users",
    "Number of active users",
)

TASKS_IN_QUEUE = Gauge(
    "tasks_in_queue",
    "Number of tasks in Celery queue",
    ["queue"],
)

MONGO_DB_CONNECTIONS = Gauge(
    "mongo_db_connections",
    "Number of MongoDB connections",
)

# Cache metrics
CACHE_HITS_TOTAL = Counter(
    "cache_hits_total",
    "Total cache hits",
    ["cache_type"],
)

CACHE_MISSES_TOTAL = Counter(
    "cache_misses_total",
    "Total cache misses",
    ["cache_type"],
)

# LLM metrics
LLM_CALLS_TOTAL = Counter(
    "llm_calls_total",
    "Total LLM API calls",
    ["model", "status"],
)

LLM_CALL_DURATION = Histogram(
    "llm_call_duration_seconds",
    "LLM API call duration in seconds",
    ["model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)


def get_metrics() -> tuple[str, str]:
    """Get Prometheus metrics in text format."""
    return generate_latest().decode("utf-8"), CONTENT_TYPE_LATEST
