"""Compute coalesced cron periods without executing any business operation."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger

TASK_KINDS = frozenset({
    "financial_check", "full_refresh", "sentiment_check", "proactive_agent",
    "daily_digest", "alert_notify", "feishu_supplier_sync",
})
# Kinds that reach a paid data provider, an LLM, or an external notification.
# They stay blocked until the permission/budget/audit gate exists.
POLICY_GATED_KINDS = frozenset({
    "full_refresh", "sentiment_check", "proactive_agent", "daily_digest",
    "alert_notify", "feishu_supplier_sync",
})


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("调度时间必须包含时区")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ScheduleDefinition:
    schedule_id: str
    revision: int
    target_id: str
    cron: str
    timezone: str
    actor_kind: str
    actor_id: str
    kind: str
    scope: dict[str, Any]
    effective_at: datetime

    def __post_init__(self) -> None:
        if not all(str(value).strip() for value in (self.schedule_id, self.target_id, self.actor_id)):
            raise ValueError("计划、目标和操作者不能为空")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("计划版本必须为正整数")
        if self.actor_kind not in {"user", "service"} or self.kind not in TASK_KINDS:
            raise ValueError("无效的操作者或调度任务种类")
        if not isinstance(self.scope, dict) or self.scope.get("target_id") != self.target_id:
            raise ValueError("任务范围必须明确绑定目标")
        _utc(self.effective_at)
        try:
            CronTrigger.from_crontab(self.cron, timezone=ZoneInfo(self.timezone))
            json.dumps(self.scope, allow_nan=False)
        except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
            raise ValueError("无效的计划时区、cron 或范围") from exc

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["effective_at"] = _utc(self.effective_at).isoformat()
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ScheduleDefinition":
        values = dict(payload)
        values["effective_at"] = datetime.fromisoformat(values["effective_at"])
        return cls(**values)


@dataclass(frozen=True)
class LogicalPeriod:
    logical_period: str
    start_at: datetime
    end_at: datetime | None
    fire_at: datetime


def _key(fire: datetime) -> str:
    # Offset and fold are intentionally excluded: the repeated local hour is
    # the same logical period even though it has two UTC occurrences.
    return fire.replace(tzinfo=None).isoformat(timespec="minutes")


def _exists(fire: datetime) -> bool:
    restored = fire.astimezone(UTC).astimezone(fire.tzinfo)
    return restored.replace(tzinfo=None) == fire.replace(tzinfo=None)


def _next(trigger: CronTrigger, previous: datetime) -> datetime | None:
    following = trigger.get_next_fire_time(previous, _utc(previous) + timedelta(microseconds=1))
    if following is not None and _utc(following) <= _utc(previous):
        raise ValueError("cron 未推进到下一触发时间")
    return following


def _period(trigger: CronTrigger, fire: datetime) -> LogicalPeriod:
    key = _key(fire)
    start = fire.replace(fold=0)
    following = _next(trigger, start)
    while following is not None and (not _exists(following) or _key(following) == key):
        following = _next(trigger, following)
    return LogicalPeriod(key, _utc(start), _utc(following) if following else None, _utc(fire))


def latest_due_period(definition: ScheduleDefinition, now: datetime) -> LogicalPeriod | None:
    """Return only the latest real occurrence since this revision took effect.

    Start near now and expand backwards only until a due occurrence exists.
    A minute schedule installed years ago therefore costs a few cron lookups.
    """
    upper, lower = _utc(now), _utc(definition.effective_at)
    if upper < lower:
        return None
    trigger = CronTrigger.from_crontab(definition.cron, timezone=ZoneInfo(definition.timezone))
    span = min(timedelta(minutes=1), upper - lower)
    while True:
        start = max(lower, upper - span)
        fire = trigger.get_next_fire_time(None, start)
        latest = None
        while fire is not None and _utc(fire) <= upper:
            if _exists(fire) and _utc(fire) >= lower:
                latest = fire
            fire = _next(trigger, fire)
        if latest is not None:
            return _period(trigger, latest)
        if start == lower:
            return None
        span = min(span * 2, upper - lower)
