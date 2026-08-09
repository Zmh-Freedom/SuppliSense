"""Lifecycle behavior for scheduler-managed Outbox polling."""

import pytest

from app.core.config import settings
from app.services import scheduler


class _FakeScheduler:
    def __init__(self) -> None:
        self.running = False
        self.jobs: dict[str, dict] = {}
        self.start_count = 0
        self.shutdown_count = 0

    def add_job(self, func, trigger: str, **kwargs: object) -> None:
        job_id = str(kwargs["id"])
        if job_id in self.jobs and not kwargs.get("replace_existing"):
            raise RuntimeError(f"duplicate job: {job_id}")
        self.jobs[job_id] = {"func": func, "trigger": trigger, **kwargs}

    def start(self) -> None:
        self.running = True
        self.start_count += 1

    def shutdown(self, wait: bool = True) -> None:
        if not self.running:
            raise RuntimeError("scheduler is not running")
        self.running = False
        self.shutdown_count += 1


class _Leader:
    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        return None


def test_scheduler_registers_one_stable_interval_outbox_job_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated startup must replace the same job rather than create duplicates or crash."""
    fake = _FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake)
    monkeypatch.setattr(scheduler, "_scheduler_leadership", _Leader())
    monkeypatch.setattr(settings, "OUTBOX_WORKER_ENABLED", True)
    monkeypatch.setattr(settings, "OUTBOX_POLL_SECONDS", 7)

    scheduler.start_scheduler()
    scheduler.start_scheduler()

    assert fake.start_count == 1
    assert list(fake.jobs) == [
        "financial_check",
        "daily_digest",
        "full_refresh",
        "sentiment_check",
        "alert_notify",
        "proactive_agent",
        "outbox_worker",
    ]
    outbox_job = fake.jobs["outbox_worker"]
    assert outbox_job["func"] is scheduler._scheduled_outbox
    assert outbox_job["trigger"] == "interval"
    assert outbox_job["seconds"] == 7
    assert outbox_job["max_instances"] == 1
    assert outbox_job["coalesce"] is True
    assert outbox_job["replace_existing"] is True


def test_scheduler_does_not_start_without_postgres_leadership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the worker that holds the advisory lock may register APScheduler jobs."""
    class NonLeader:
        def acquire(self) -> bool:
            return False

        def release(self) -> None:
            raise AssertionError("non-leader must not release an unheld lock")

    fake = _FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake)
    monkeypatch.setattr(scheduler, "_scheduler_leadership", NonLeader(), raising=False)

    scheduler.start_scheduler()

    assert fake.jobs == {}
    assert fake.start_count == 0


def test_postgres_scheduler_advisory_lock_allows_only_one_leader() -> None:
    """A replacement worker can take over only after the prior scheduler session releases its lock."""
    from app.services.scheduler_leadership import SchedulerLeadership

    first = SchedulerLeadership()
    second = SchedulerLeadership()
    try:
        assert first.acquire() is True
        assert second.acquire() is False
        first.release()
        assert second.acquire() is True
    finally:
        first.release()
        second.release()


def test_scheduler_keeps_metrics_refresh_job_when_disabled_and_stop_is_safe_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rollback switch disables delivery while retaining periodic backlog metric refreshes."""
    fake = _FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake)
    monkeypatch.setattr(scheduler, "_scheduler_leadership", _Leader())
    monkeypatch.setattr(settings, "OUTBOX_WORKER_ENABLED", False)

    scheduler.stop_scheduler()
    scheduler.start_scheduler()
    scheduler.stop_scheduler()
    scheduler.stop_scheduler()

    assert "outbox_worker" in fake.jobs
    assert fake.start_count == 1
    assert fake.shutdown_count == 1
