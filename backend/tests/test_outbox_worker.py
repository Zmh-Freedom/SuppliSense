"""Behavior coverage for the synchronous Outbox worker and its metrics."""

import re

import pytest

from app.core import metrics
from app.core.config import Settings, settings
from app.domains.company import service as company_service
from app.domains.outbox import repo as outbox_repo
from app.domains.outbox import service as outbox_service
from app.domains.outbox import worker as outbox_worker


def _counter_value(counter, **labels: str) -> float:
    return counter.labels(**labels)._value.get()


def test_production_gunicorn_enforces_single_process_metrics_topology() -> None:
    """Process-local Prometheus collectors require the production web process count to be one."""
    from pathlib import Path
    from runpy import run_path

    config = run_path(Path(__file__).parents[1] / "gunicorn.conf.py")

    assert config["workers"] == 1


def test_worker_disabled_still_refreshes_pending_metrics_without_consuming(monkeypatch) -> None:
    """The rollback switch stops delivery only; metrics must still expose the pending backlog."""
    monkeypatch.setattr(outbox_worker.settings, "OUTBOX_WORKER_ENABLED", False)
    monkeypatch.setattr(
        outbox_worker,
        "process_outbox_batch",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("disabled worker must not consume")),
    )
    monkeypatch.setattr(
        outbox_worker.repo,
        "get_pending_stats",
        lambda: {"pending": 7, "oldest_age_seconds": 19.5},
    )

    result = outbox_worker.run_outbox_once()

    assert result["status"] == "ok"
    assert result["claimed"] == 0
    assert metrics.OUTBOX_PENDING_EVENTS._value.get() == 7
    assert metrics.OUTBOX_OLDEST_PENDING_AGE_SECONDS._value.get() == 19.5


def test_outbox_settings_have_safe_defaults_and_reject_non_positive_limits() -> None:
    """Zero polling, batch, attempt, or lease values would create a broken worker."""
    configured = Settings(
        OUTBOX_WORKER_ENABLED=False,
        OUTBOX_POLL_SECONDS=7,
        OUTBOX_BATCH_SIZE=25,
        OUTBOX_MAX_ATTEMPTS=6,
        OUTBOX_LEASE_SECONDS=45,
    )

    assert configured.OUTBOX_WORKER_ENABLED is False
    assert (
        configured.OUTBOX_POLL_SECONDS,
        configured.OUTBOX_BATCH_SIZE,
        configured.OUTBOX_MAX_ATTEMPTS,
        configured.OUTBOX_LEASE_SECONDS,
    ) == (7, 25, 6, 45)

    with pytest.raises(ValueError):
        Settings(OUTBOX_BATCH_SIZE=0)


def test_worker_uses_configured_limits_and_unique_hostname_pid_worker_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing an ID or ignoring a configured lease would break concurrent workers."""
    calls: list[dict] = []

    def process_outbox_batch(**kwargs: object) -> dict:
        calls.append(kwargs)
        return {"claimed": 0, "published": 0, "failed": 0}

    monkeypatch.setattr(outbox_worker, "process_outbox_batch", process_outbox_batch)
    monkeypatch.setattr(outbox_repo, "get_pending_stats", lambda: {"pending": 0, "oldest_age_seconds": None})
    monkeypatch.setattr(settings, "OUTBOX_BATCH_SIZE", 25)
    monkeypatch.setattr(settings, "OUTBOX_MAX_ATTEMPTS", 6)
    monkeypatch.setattr(settings, "OUTBOX_LEASE_SECONDS", 45)

    first = outbox_worker.run_outbox_once()
    second = outbox_worker.run_outbox_once()

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert first["worker_id"] != second["worker_id"]
    assert re.fullmatch(r"[^:]+:\d+:[0-9a-f]{8}", first["worker_id"])
    assert [
        (call["batch_size"], call["max_attempts"], call["lease_seconds"])
        for call in calls
    ] == [(25, 6, 45), (25, 6, 45)]


def test_worker_refreshes_pending_metrics_and_resets_empty_backlog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leaving the previous backlog age after it drains makes alerts misleading."""
    stats = iter((
        {"pending": 3, "oldest_age_seconds": 12.5},
        {"pending": 0, "oldest_age_seconds": None},
    ))
    monkeypatch.setattr(
        outbox_worker,
        "process_outbox_batch",
        lambda **kwargs: {"claimed": 0, "published": 0, "failed": 0},
    )
    monkeypatch.setattr(outbox_repo, "get_pending_stats", lambda: next(stats))

    outbox_worker.run_outbox_once()
    assert metrics.OUTBOX_PENDING_EVENTS._value.get() == 3
    assert metrics.OUTBOX_OLDEST_PENDING_AGE_SECONDS._value.get() == 12.5

    outbox_worker.run_outbox_once()
    assert metrics.OUTBOX_PENDING_EVENTS._value.get() == 0
    assert metrics.OUTBOX_OLDEST_PENDING_AGE_SECONDS._value.get() == 0


def test_worker_records_terminal_outcomes_without_counting_a_dead_letter_as_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead letter must be terminal, while only a scheduled retry increments retries."""
    def process_outbox_batch(**kwargs: object) -> dict:
        observer = kwargs["outcome_observer"]
        observer("company.created", "published")
        observer("company.updated", "retry")
        observer("company.verified", "dead_lettered")
        return {"claimed": 3, "published": 1, "failed": 2}

    monkeypatch.setattr(outbox_worker, "process_outbox_batch", process_outbox_batch)
    monkeypatch.setattr(outbox_repo, "get_pending_stats", lambda: {"pending": 2, "oldest_age_seconds": 1})
    published_before = _counter_value(
        metrics.OUTBOX_EVENTS_PROCESSED_TOTAL,
        event_type="company.created",
        status="published",
    )
    dead_before = _counter_value(
        metrics.OUTBOX_EVENTS_PROCESSED_TOTAL,
        event_type="company.verified",
        status="dead_lettered",
    )
    retry_before = _counter_value(metrics.OUTBOX_RETRIES_TOTAL, event_type="company.updated")
    dead_retry_before = _counter_value(metrics.OUTBOX_RETRIES_TOTAL, event_type="company.verified")

    outbox_worker.run_outbox_once()

    assert _counter_value(
        metrics.OUTBOX_EVENTS_PROCESSED_TOTAL,
        event_type="company.created",
        status="published",
    ) == published_before + 1
    assert _counter_value(
        metrics.OUTBOX_EVENTS_PROCESSED_TOTAL,
        event_type="company.verified",
        status="dead_lettered",
    ) == dead_before + 1
    assert _counter_value(metrics.OUTBOX_RETRIES_TOTAL, event_type="company.updated") == retry_before + 1
    assert _counter_value(metrics.OUTBOX_RETRIES_TOTAL, event_type="company.verified") == dead_retry_before


def test_worker_logs_failure_returns_detectable_status_and_still_refreshes_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One database error must not kill future interval invocations or leave stale gauges."""
    def fail_process(**kwargs: object) -> dict:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(outbox_worker, "process_outbox_batch", fail_process)
    monkeypatch.setattr(outbox_repo, "get_pending_stats", lambda: {"pending": 0, "oldest_age_seconds": None})

    result = outbox_worker.run_outbox_once()

    assert result["status"] == "failed"
    assert result["claimed"] == 0
    assert metrics.OUTBOX_PENDING_EVENTS._value.get() == 0
    assert metrics.OUTBOX_OLDEST_PENDING_AGE_SECONDS._value.get() == 0


def test_process_batch_reports_retry_and_dead_letter_as_distinct_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing the failure branch must not report a terminal dead letter as a retry."""
    event = {
        "event_id": "event-1",
        "event_type": "company.created",
        "attempt_count": 0,
    }
    outcomes: list[tuple[str, str]] = []
    monkeypatch.setattr(outbox_service.repo, "claim_events", lambda *args: [event])
    monkeypatch.setattr(outbox_service.repo, "is_consumed", lambda *args: False)
    monkeypatch.setattr(outbox_service.repo, "record_consumption", lambda *args: True)
    monkeypatch.setattr(outbox_service.repo, "mark_failed", lambda *args: True)
    monkeypatch.setattr(
        outbox_service,
        "_CONSUMERS",
        {("company.created", "failing-test-consumer"): lambda value: (_ for _ in ()).throw(RuntimeError("fail"))},
    )

    outbox_service.process_outbox_batch(
        "test-worker",
        1,
        2,
        60,
        outcome_observer=lambda event_type, outcome: outcomes.append((event_type, outcome)),
    )
    outbox_service.process_outbox_batch(
        "test-worker",
        1,
        1,
        60,
        outcome_observer=lambda event_type, outcome: outcomes.append((event_type, outcome)),
    )

    assert outcomes == [("company.created", "retry"), ("company.created", "dead_lettered")]


def test_identity_resolution_metric_uses_the_bounded_resolution_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity query text must not become a metric label, but its result must be counted."""
    monkeypatch.setattr(
        company_service.company_repo,
        "search_identity_rows",
        lambda query, limit: ([], False),
    )
    before = _counter_value(
        metrics.COMPANY_IDENTITY_RESOLUTIONS_TOTAL,
        resolution="pending_verification",
    )

    result = company_service.search_identity("unmatched supplier")

    assert result == {"resolution": "pending_verification", "exact": None, "candidates": []}
    assert _counter_value(
        metrics.COMPANY_IDENTITY_RESOLUTIONS_TOTAL,
        resolution="pending_verification",
    ) == before + 1
