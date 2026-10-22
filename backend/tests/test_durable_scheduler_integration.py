"""Real PostgreSQL durability and deduplication for scheduled execution jobs."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest

from app.core.config import settings
from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_cursor
from app.domains.scheduling.periods import ScheduleDefinition
from app.domains.scheduling.repo import (
    enqueue_due_schedule,
    list_due_schedules,
    register_schedule,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 22, 4, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def scheduler_schema():
    if settings.PG_DB != "scheduler_stage3_test":
        pytest.skip("requires the isolated scheduler_stage3_test PostgreSQL database")
    ensure_pg_schema()


@pytest.fixture
def schedule_factory(scheduler_schema):
    created: list[str] = []

    def create(**changes) -> ScheduleDefinition:
        schedule_id = changes.pop("schedule_id", f"test-{uuid4()}")
        values = {
            "schedule_id": schedule_id,
            "revision": 1,
            "target_id": "target-1",
            "cron": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "actor_kind": "user",
            "actor_id": str(uuid4()),
            "kind": "financial_check",
            "scope": {"target_id": "target-1"},
            "effective_at": NOW - timedelta(days=2),
        }
        values.update(changes)
        definition = ScheduleDefinition(**values)
        register_schedule(definition)
        created.append(definition.schedule_id)
        return definition

    yield create

    if created:
        with get_cursor() as (_, cur):
            cur.execute(
                "DELETE FROM scheduled_execution_jobs WHERE schedule_id = ANY(%s)",
                (created,),
            )
            cur.execute(
                "DELETE FROM durable_schedules WHERE schedule_id = ANY(%s)", (created,)
            )


def _jobs(schedule_id: str) -> list[tuple]:
    with get_cursor() as (_, cur):
        cur.execute(
            """SELECT schedule_revision, target_id, logical_period, status
               FROM scheduled_execution_jobs WHERE schedule_id=%s
               ORDER BY schedule_revision, logical_period""",
            (schedule_id,),
        )
        return cur.fetchall()


def test_concurrent_schedulers_create_exactly_one_period_job(schedule_factory):
    schedule = schedule_factory()
    barrier = Barrier(2)

    def enqueue():
        barrier.wait()
        return enqueue_due_schedule(schedule.schedule_id, schedule.target_id, now=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: enqueue(), range(2)))

    assert results[0]["job_id"] == results[1]["job_id"]
    assert sorted(result["created"] for result in results) == [False, True]
    assert _jobs(schedule.schedule_id) == [(1, "target-1", "2026-10-22T09:00", "QUEUED")]


def test_repeat_enqueue_returns_existing_job_without_mutating_it(schedule_factory):
    schedule = schedule_factory()
    first = enqueue_due_schedule(schedule.schedule_id, schedule.target_id, now=NOW)
    second = enqueue_due_schedule(schedule.schedule_id, schedule.target_id, now=NOW + timedelta(hours=20))
    assert second["job_id"] == first["job_id"]
    assert second["created"] is False
    assert second["logical_period"] == first["logical_period"]


def test_new_revision_creates_new_period_job_and_old_revision_is_immutable(schedule_factory):
    first = schedule_factory()
    enqueue_due_schedule(first.schedule_id, first.target_id, now=NOW)
    register_schedule(first)
    with pytest.raises(ValueError):
        register_schedule(
            ScheduleDefinition(
                **{**first.__dict__, "cron": "30 9 * * *"}
            )
        )
    second = ScheduleDefinition(**{**first.__dict__, "revision": 2, "cron": "30 9 * * *"})
    register_schedule(second)
    job = enqueue_due_schedule(first.schedule_id, first.target_id, now=NOW)
    assert job["schedule_revision"] == 2
    assert _jobs(first.schedule_id) == [
        (1, "target-1", "2026-10-22T09:00", "QUEUED"),
        (2, "target-1", "2026-10-22T09:30", "QUEUED"),
    ]
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT cron FROM durable_schedules WHERE schedule_id=%s ORDER BY revision",
            (first.schedule_id,),
        )
        assert cur.fetchall() == [("0 9 * * *",), ("30 9 * * *",)]


def test_paid_kind_is_recorded_but_blocked_from_execution(schedule_factory):
    schedule = schedule_factory(kind="full_refresh")
    job = enqueue_due_schedule(schedule.schedule_id, schedule.target_id, now=NOW)
    assert job["status"] == "BLOCKED_POLICY"
    assert _jobs(schedule.schedule_id)[0][3] == "BLOCKED_POLICY"


def test_no_due_period_and_unknown_target_do_not_create_jobs(schedule_factory):
    schedule = schedule_factory(effective_at=NOW + timedelta(days=1))
    assert enqueue_due_schedule(schedule.schedule_id, schedule.target_id, now=NOW) is None
    ready = schedule_factory()
    assert enqueue_due_schedule(ready.schedule_id, "foreign-target", now=NOW) is None
    assert _jobs(ready.schedule_id) == []


def test_disabled_schedule_is_not_enqueued(schedule_factory):
    schedule = schedule_factory()
    with get_cursor() as (_, cur):
        cur.execute(
            "UPDATE durable_schedules SET enabled=FALSE WHERE schedule_id=%s AND revision=1",
            (schedule.schedule_id,),
        )
    assert enqueue_due_schedule(schedule.schedule_id, schedule.target_id, now=NOW) is None


def test_due_schedule_rotation_does_not_starve_later_rows(schedule_factory):
    schedules = [schedule_factory() for _ in range(3)]
    # Enqueueing an older row advances only its own cursor.
    enqueue_due_schedule(schedules[0].schedule_id, "target-1", now=NOW)
    ids = list_due_schedules(batch_size=1000)
    positions = [ids.index((schedule.schedule_id, "target-1")) for schedule in schedules]
    assert positions[0] > positions[1]
    assert positions[0] > positions[2]


def test_failed_enqueue_rolls_back_job_and_cursor(schedule_factory, monkeypatch):
    schedule = schedule_factory()
    from app.domains.scheduling import repo

    def broken(definition, now):
        from app.domains.scheduling.periods import LogicalPeriod
        from datetime import datetime, timezone
        return LogicalPeriod(None, datetime(2026, 10, 22, tzinfo=timezone.utc), None, datetime(2026, 10, 22, tzinfo=timezone.utc))

    monkeypatch.setattr(repo, "latest_due_period", broken)
    with pytest.raises(Exception):
        enqueue_due_schedule(schedule.schedule_id, schedule.target_id, now=NOW)
    assert _jobs(schedule.schedule_id) == []
    with get_cursor() as (_, cur):
        cur.execute("SELECT last_checked_at FROM durable_schedules WHERE schedule_id=%s", (schedule.schedule_id,))
        assert cur.fetchone()[0] is None
