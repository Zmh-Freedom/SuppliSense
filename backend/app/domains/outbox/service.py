"""Outbox delivery, retry, replay, and consumer registration behavior."""

from collections.abc import Callable

from app.core.errors import DomainError
from app.core.logging import get_logger
from app.db.postgres import get_cursor
from app.domains.auth.audit_repo import create_log_with_cursor
from app.domains.outbox import repo
from app.domains.outbox.sanitization import sanitize_delivery_error

logger = get_logger(__name__)

_CONSUMERS: dict[tuple[str, str], Callable[[dict], None]] = {}


def register_consumer(
    event_type: str,
    consumer_name: str,
    handler: Callable[[dict], None],
) -> None:
    key = (event_type, consumer_name)
    registered_handler = _CONSUMERS.get(key)
    if registered_handler is not None and registered_handler is not handler:
        raise ValueError("消费者已注册且处理函数不同")
    _CONSUMERS[key] = handler


def retry_delay_seconds(attempt: int) -> int:
    return min(2 ** attempt, 300)


def process_outbox_batch(
    worker_id: str,
    batch_size: int,
    max_attempts: int,
    lease_seconds: int,
    outcome_observer: Callable[[str, str], None] | None = None,
) -> dict:
    if batch_size < 1:
        raise ValueError("batch_size 必须大于 0")
    if max_attempts < 1:
        raise ValueError("max_attempts 必须大于 0")
    if lease_seconds < 1:
        raise ValueError("lease_seconds 必须大于 0")

    events = repo.claim_events(worker_id, batch_size, lease_seconds)
    result = {"claimed": len(events), "published": 0, "failed": 0}
    for event in events:
        event_id = event["event_id"]
        try:
            for (event_type, consumer_name), handler in _CONSUMERS.items():
                if event_type != event["event_type"]:
                    continue
                if repo.is_consumed(event_id, consumer_name):
                    continue
                handler(event)
                repo.record_consumption(event_id, consumer_name)
            if repo.mark_published(event_id, worker_id):
                result["published"] += 1
                _notify_outcome(outcome_observer, event["event_type"], "published")
            else:
                logger.info(
                    "outbox_event_lease_lost",
                    event_id=event_id,
                    event_type=event["event_type"],
                    worker_id=worker_id,
                )
        except Exception as exc:
            attempt = int(event["attempt_count"]) + 1
            safe_error = sanitize_delivery_error(exc)
            if repo.mark_failed(
                event_id,
                safe_error,
                max_attempts,
                retry_delay_seconds(attempt),
                worker_id,
            ):
                logger.error(
                    "outbox_event_failed",
                    event_id=event_id,
                    event_type=event["event_type"],
                    attempt=attempt,
                    error=safe_error,
                )
                result["failed"] += 1
                outcome = "dead_lettered" if attempt >= max_attempts else "retry"
                _notify_outcome(outcome_observer, event["event_type"], outcome)
            else:
                logger.info(
                    "outbox_event_lease_lost",
                    event_id=event_id,
                    event_type=event["event_type"],
                    worker_id=worker_id,
                )
    return result


def _notify_outcome(
    observer: Callable[[str, str], None] | None,
    event_type: str,
    outcome: str,
) -> None:
    """Keep telemetry failures from changing already-persisted delivery state."""
    if observer is None:
        return
    try:
        observer(event_type, outcome)
    except Exception:
        logger.exception(
            "outbox_outcome_observer_failed",
            event_type=event_type,
            outcome=outcome,
        )


def list_events(status: str, limit: int) -> list[dict]:
    events = repo.list_events(status, limit)
    return [
        to_admin_event(event)
        for event in sorted(events, key=lambda event: (event["occurred_at"], event["event_id"]))
    ]


def to_admin_event(event: dict) -> dict:
    """Map a repository row to the deliberately minimal administrator view."""
    return {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "aggregate_type": event["aggregate_type"],
        "aggregate_id": event["aggregate_id"],
        "schema_version": event["schema_version"],
        "status": event.get("status", _event_status(event)),
        "attempt_count": event["attempt_count"],
        "occurred_at": event["occurred_at"],
        "published_at": event["published_at"],
        "last_error": (
            sanitize_delivery_error(event["last_error"])
            if event.get("last_error") is not None
            else None
        ),
    }


def replay_event(event_id: str, reason: str, actor_id: str | None) -> dict:
    with get_cursor() as (_, cur):
        event = repo.replay_event_with_cursor(cur, event_id)
        if event is None:
            existing_event = repo.get_replay_state_with_cursor(cur, event_id)
            if existing_event is None:
                raise DomainError("OUTBOX_EVENT_NOT_FOUND", "Outbox 事件不存在", 404)
            if existing_event["lease_active"]:
                raise DomainError(
                    "OUTBOX_EVENT_LEASE_ACTIVE",
                    "Outbox 事件正在被 worker 处理，不能回放",
                    409,
                    {"event_id": event_id},
                )
            raise DomainError(
                "OUTBOX_EVENT_NOT_REPLAYABLE",
                "仅可回放未发布的失败或死信事件",
                409,
                {"event_id": event_id},
            )
        create_log_with_cursor(
            cur,
            action="outbox.replayed",
            user_id=actor_id,
            resource_type="outbox_event",
            resource_id=event_id,
            details={"reason": reason},
        )
    return {"event_id": event_id, "status": "queued", "reason": reason}


def _event_status(event: dict) -> str:
    if event.get("published_at") is not None:
        return "published"
    if event.get("dead_lettered_at") is not None:
        return "dead_letter"
    if event.get("last_error") is not None:
        return "failed"
    return "pending"


def _company_event_audit(event: dict) -> None:
    logger.info(
        "company_event_consumed",
        event_id=event["event_id"],
        event_type=event["event_type"],
    )


for _event_type in (
    "company.created",
    "company.updated",
    "company.verified",
    "company.merged",
):
    register_consumer(_event_type, "company_event_audit", _company_event_audit)
