"""Timezone and coalescing contracts for the durable scheduler."""

from dataclasses import replace
from datetime import datetime

import pytest

from app.domains.scheduling.periods import ScheduleDefinition, latest_due_period


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


def definition(**changes) -> ScheduleDefinition:
    values = {
        "schedule_id": "risk-daily", "revision": 1, "target_id": "target-1",
        "cron": "0 9 * * *", "timezone": "Asia/Shanghai", "actor_kind": "user",
        "actor_id": "00000000-0000-4000-8000-000000000001", "kind": "financial_check", "scope": {"target_id": "target-1"},
        "effective_at": instant("2026-10-20T00:00:00+00:00"),
    }
    return ScheduleDefinition(**{**values, **changes})


def test_daily_period_has_local_key_and_utc_boundaries():
    period = latest_due_period(definition(), instant("2026-10-22T04:00:00+00:00"))
    assert period.logical_period == "2026-10-22T09:00"
    assert period.start_at == instant("2026-10-22T01:00:00+00:00")
    assert period.end_at == instant("2026-10-23T01:00:00+00:00")


def test_missed_periods_only_return_the_latest_due_occurrence():
    period = latest_due_period(definition(), instant("2026-10-25T04:00:00+00:00"))
    assert period.logical_period == "2026-10-25T09:00"
    assert latest_due_period(definition(), instant("2026-10-19T04:00:00+00:00")) is None
    assert latest_due_period(
        definition(effective_at=instant("2026-10-22T01:01:00+00:00")),
        instant("2026-10-22T04:00:00+00:00"),
    ) is None


def test_dst_repeated_hour_has_one_logical_period_and_stable_boundaries():
    schedule = definition(cron="30 1 * * *", timezone="America/New_York",
                          effective_at=instant("2026-10-31T00:00:00+00:00"))
    first = latest_due_period(schedule, instant("2026-11-01T05:45:00+00:00"))
    second = latest_due_period(schedule, instant("2026-11-01T06:45:00+00:00"))
    assert first.logical_period == second.logical_period == "2026-11-01T01:30"
    assert first.start_at == second.start_at == instant("2026-11-01T05:30:00+00:00")
    assert first.end_at == second.end_at == instant("2026-11-02T06:30:00+00:00")
    assert second.fire_at == instant("2026-11-01T06:30:00+00:00")


def test_dst_missing_local_hour_is_skipped():
    schedule = definition(cron="30 2 * * *", timezone="America/New_York",
                          effective_at=instant("2026-03-07T00:00:00+00:00"))
    period = latest_due_period(schedule, instant("2026-03-08T08:00:00+00:00"))
    assert period.logical_period == "2026-03-07T02:30"
    assert period.end_at == instant("2026-03-09T06:30:00+00:00")


def test_long_downtime_with_minute_cron_does_not_replay_every_period(monkeypatch):
    from apscheduler.triggers.cron import CronTrigger

    calls = []
    original = CronTrigger.get_next_fire_time

    def counted(self, previous, now):
        calls.append(now)
        return original(self, previous, now)

    monkeypatch.setattr(CronTrigger, "get_next_fire_time", counted)
    schedule = definition(cron="* * * * *", effective_at=instant("2000-01-01T00:00:00+00:00"))
    period = latest_due_period(schedule, instant("2026-10-22T04:00:30+00:00"))
    assert period.logical_period == "2026-10-22T12:00"
    assert len(calls) < 25


@pytest.mark.parametrize("changes", [
    {"revision": 0}, {"timezone": "Missing/Zone"}, {"cron": "not cron"},
    {"actor_kind": "implicit-admin"}, {"actor_id": ""}, {"kind": "unknown"},
    {"scope": {"target_id": "another"}}, {"effective_at": datetime(2026, 10, 22)},
    {"actor_kind": "user", "actor_id": "not-a-uuid"},
])
def test_invalid_schedule_definition_is_rejected(changes):
    with pytest.raises(ValueError):
        definition(**changes)


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        latest_due_period(definition(), datetime(2026, 10, 22))


def test_leap_day_and_explicit_weekday_use_cron_semantics():
    schedule = definition(cron="0 9 29 2 *", effective_at=instant("2020-01-01T00:00:00+00:00"))
    period = latest_due_period(schedule, instant("2026-10-22T04:00:00+00:00"))
    assert period.logical_period == "2024-02-29T09:00"
    assert period.end_at == instant("2028-02-29T01:00:00+00:00")
    weekly = replace(definition(), cron="0 9 * * mon")
    assert latest_due_period(weekly, instant("2026-10-28T04:00:00+00:00")).logical_period == "2026-10-26T09:00"
