"""Outbox delivery, retry, replay, and consumer registration behavior."""

from collections.abc import Callable

from app.core.errors import DomainError
from app.core.logging import get_logger
from app.db.postgres import get_cursor
from app.domains.auth.audit_repo import create_log_with_cursor
from app.domains.outbox import repo

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
            else:
                logger.info(
                    "outbox_event_lease_lost",
                    event_id=event_id,
                    event_type=event["event_type"],
                    worker_id=worker_id,
                )
        except Exception as exc:
            attempt = int(event["attempt_count"]) + 1
            if repo.mark_failed(
                event_id,
                str(exc),
                max_attempts,
                retry_delay_seconds(attempt),
                worker_id,
            ):
                logger.exception(
                    "outbox_event_failed",
                    event_id=event_id,
                    event_type=event["event_type"],
                    attempt=attempt,
                )
                result["failed"] += 1
            else:
                logger.info(
                    "outbox_event_lease_lost",
                    event_id=event_id,
                    event_type=event["event_type"],
                    worker_id=worker_id,
                )
    return result


def list_events(status: str, limit: int) -> list[dict]:
    return repo.list_events(status, limit)


def replay_event(event_id: str, reason: str, actor_id: str | None) -> dict:
    with get_cursor() as (_, cur):
        event = repo.replay_event_with_cursor(cur, event_id)
        if event is None:
            existing_event = repo.get_event(event_id)
            if existing_event is None:
                raise DomainError("OUTBOX_EVENT_NOT_FOUND", "Outbox 事件不存在", 404)
            raise DomainError("OUTBOX_REPLAY_NOT_ALLOWED", "只有死信事件可以重放", 409)
        create_log_with_cursor(
            cur,
            action="outbox.replayed",
            user_id=actor_id,
            resource_type="outbox_event",
            resource_id=event_id,
            details={"reason": reason},
        )
    return {"event_id": event_id, "status": "queued", "reason": reason}


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
