"""Real PostgreSQL leases, fencing and audit for scheduled execution jobs."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.config import settings
from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_cursor
from app.domains.scheduling import repo
from app.domains.scheduling.periods import ScheduleDefinition

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 22, 4, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_scheduler_tables():
    if settings.PG_DB != "scheduler_stage3_test":
        pytest.skip("requires the isolated scheduler_stage3_test PostgreSQL database")
    ensure_pg_schema()
    with get_cursor() as (_, cur):
        cur.execute("DELETE FROM scheduled_execution_jobs")
        cur.execute("DELETE FROM durable_schedules")
    yield
    with get_cursor() as (_, cur):
        cur.execute("DELETE FROM scheduled_execution_jobs")
        cur.execute("DELETE FROM durable_schedules")


def _schedule(kind="financial_check", **changes) -> ScheduleDefinition:
    values = {
        "schedule_id": f"test-{uuid4()}", "revision": 1, "target_id": "target-1",
        "cron": "0 9 * * *", "timezone": "Asia/Shanghai", "actor_kind": "user",
        "actor_id": str(uuid4()), "kind": kind, "scope": {"target_id": "target-1"},
        "effective_at": NOW - timedelta(days=2),
    }
    values.update(changes)
    definition = ScheduleDefinition(**values)
    repo.register_schedule(definition)
    return definition


def _enqueue(definition):
    job = repo.enqueue_due_schedule(definition.schedule_id, definition.target_id, now=NOW)
    assert job is not None
    return job


def _expire_lease(job_id: str):
    with get_cursor() as (_, cur):
        cur.execute(
            "UPDATE scheduled_execution_jobs SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE id=%s",
            (job_id,),
        )


def _row(job_id: str):
    with get_cursor() as (_, cur):
        cur.execute(
            """SELECT status,attempts,fencing_token,last_error,completed_at
               FROM scheduled_execution_jobs WHERE id=%s""",
            (job_id,),
        )
        return cur.fetchone()


def test_expired_lease_is_fenced_and_the_old_owner_cannot_finish():
    definition = _schedule()
    queued = _enqueue(definition)
    first = repo.claim_scheduled_job("worker-a", lease_seconds=30)
    assert first["id"] == queued["job_id"]
    assert repo.heartbeat_scheduled_job(first["id"], "worker-a", first["fencing_token"], lease_seconds=30)
    assert not repo.heartbeat_scheduled_job(first["id"], "worker-b", first["fencing_token"], lease_seconds=30)

    _expire_lease(first["id"])
    second = repo.claim_scheduled_job("worker-b", lease_seconds=30)
    assert second["id"] == first["id"]
    assert second["attempts"] == 2
    assert second["fencing_token"] > first["fencing_token"]
    assert repo.finish_scheduled_job(first, "worker-a", "SUCCEEDED") is False
    assert repo.finish_scheduled_job(
        second, "worker-b", "SUCCEEDED", result={"ok": True}
    ) is True
    status, attempts, _token, error, completed = _row(first["id"])
    assert (status, attempts, error) == ("SUCCEEDED", 2, None)
    assert completed is not None
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT details FROM audit_logs WHERE resource_id=%s AND action='scheduled.execution.finished'",
            (first["id"],),
        )
        audit = cur.fetchone()
    assert audit is not None and audit[0]["status"] == "SUCCEEDED"


def test_attempt_limit_stops_reclaiming_and_requires_review():
    definition = _schedule()
    queued = _enqueue(definition)
    with get_cursor() as (_, cur):
        cur.execute("UPDATE scheduled_execution_jobs SET max_attempts=1 WHERE id=%s", (queued["job_id"],))
    first = repo.claim_scheduled_job("worker-a", lease_seconds=30)
    assert first["attempts"] == 1
    _expire_lease(first["id"])
    assert repo.claim_scheduled_job("worker-b", lease_seconds=30) is None
    status, attempts, _token, error, _completed = _row(first["id"])
    assert (status, attempts, error) == ("NEEDS_REVIEW", 1, "attempt_limit_exceeded")


def test_paid_job_is_queued_only_when_the_switch_is_enabled(monkeypatch):
    definition = _schedule(kind="full_refresh")
    blocked = _enqueue(definition)
    assert blocked["status"] == "BLOCKED_POLICY"
    monkeypatch.setattr(settings, "SCHEDULER_PAID_EXECUTION_ENABLED", True)
    ready = repo.enqueue_due_schedule(definition.schedule_id, definition.target_id, now=NOW + timedelta(days=1))
    assert ready["status"] == "QUEUED"
    assert repo.claim_scheduled_job("worker-a") is not None


def test_worker_executes_a_free_job_with_audit(monkeypatch):
    definition = _schedule()
    queued = _enqueue(definition)
    from app.domains.scheduling import executor
    from app.workers import scheduled_worker

    monkeypatch.setattr(executor, "_target_for", lambda _d: {"monitor_target_id": "target-1"})
    monkeypatch.setitem(executor.HANDLERS, "financial_check", lambda target, defn: {"changed": False})
    result = scheduled_worker.run_scheduled_worker_once("worker-test")
    assert result["executed"] == 1 and result["succeeded"] is True
    status, attempts, _token, error, _completed = _row(queued["job_id"])
    assert (status, attempts, error) == ("SUCCEEDED", 1, None)
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT details FROM audit_logs WHERE resource_id=%s AND action='scheduled.execution.finished'",
            (queued["job_id"],),
        )
        assert cur.fetchone()[0]["target_id"] == "target-1"


def test_worker_marks_paid_provider_failure_for_review(monkeypatch):
    definition = _schedule(kind="full_refresh")
    monkeypatch.setattr(settings, "SCHEDULER_PAID_EXECUTION_ENABLED", True)
    queued = _enqueue(definition)
    from app.core import paid_quota
    from app.domains.scheduling import executor
    from app.workers import scheduled_worker

    monkeypatch.setattr(executor, "_target_for", lambda _d: {"monitor_target_id": "target-1"})
    monkeypatch.setattr(paid_quota, "reserve_paid_refresh_once", lambda *a: True)
    monkeypatch.setitem(
        executor.HANDLERS, "full_refresh",
        lambda target, defn: (_ for _ in ()).throw(RuntimeError("provider timeout")),
    )
    result = scheduled_worker.run_scheduled_worker_once("worker-paid")
    assert result["status"] == "NEEDS_REVIEW"
    status, _attempts, _token, error, _completed = _row(queued["job_id"])
    assert status == "NEEDS_REVIEW"
    assert error == "provider timeout"


def test_paid_job_with_started_provider_is_not_reclaimed(monkeypatch):
    definition = _schedule(kind="full_refresh")
    monkeypatch.setattr(settings, "SCHEDULER_PAID_EXECUTION_ENABLED", True)
    queued = _enqueue(definition)
    first = repo.claim_scheduled_job("worker-a", lease_seconds=30)
    assert first is not None
    repo.mark_provider_started(first, "worker-a")
    _expire_lease(first["id"])
    assert repo.claim_scheduled_job("worker-b", lease_seconds=30) is None
    status, _attempts, _token, error, completed = _row(queued["job_id"])
    assert status == "NEEDS_REVIEW"
    assert error == "external_result_unknown"
    assert completed is not None


def test_paid_job_crash_before_provider_can_be_reclaimed(monkeypatch):
    definition = _schedule(kind="full_refresh")
    monkeypatch.setattr(settings, "SCHEDULER_PAID_EXECUTION_ENABLED", True)
    _enqueue(definition)
    first = repo.claim_scheduled_job("worker-a", lease_seconds=30)
    assert first is not None
    _expire_lease(first["id"])
    second = repo.claim_scheduled_job("worker-b", lease_seconds=30)
    assert second is not None and second["id"] == first["id"]
    assert second["attempts"] == 2
