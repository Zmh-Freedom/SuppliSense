"""Real PostgreSQL coverage for transactional Outbox behavior."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from uuid import UUID

import pytest

from app.core.errors import DomainError
from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_conn, get_cursor, put_conn
from app.domains.outbox import repo as outbox_repo
from app.domains.outbox import service as outbox_service
from app.domains.outbox.repo import (
    claim_events,
    enqueue_event,
    mark_failed,
    mark_published,
    record_consumption,
)
from app.domains.outbox.service import (
    list_events,
    process_outbox_batch,
    register_consumer,
    replay_event,
    retry_delay_seconds,
)


@contextmanager
def _real_connection() -> Iterator[tuple[object, object]]:
    conn = get_conn()
    cur = None
    try:
        conn.rollback()
        cur = conn.cursor()
        yield conn, cur
    finally:
        if cur is not None:
            cur.close()
        conn.rollback()
        put_conn(conn)


def _delete_event(event_id: str) -> None:
    with get_cursor() as (_, cur):
        cur.execute("DELETE FROM outbox_consumptions WHERE event_id = %s", (event_id,))
        cur.execute("DELETE FROM outbox_events WHERE event_id = %s", (event_id,))
        cur.execute(
            "DELETE FROM audit_logs WHERE action = %s AND resource_id = %s",
            ("outbox.replayed", event_id),
        )


def _enqueue_committed(
    monkeypatch: pytest.MonkeyPatch,
    event_id: UUID,
    event_type: str,
    aggregate_id: UUID,
    payload: dict,
) -> str:
    monkeypatch.setattr(outbox_repo, "uuid4", lambda: event_id)
    with get_cursor() as (_, cur):
        return enqueue_event(
            cur,
            event_type,
            "company",
            str(aggregate_id),
            payload,
        )


def test_enqueue_event_lets_caller_commit_the_real_postgres_transaction(monkeypatch):
    """An internal commit would expose the event before the caller commits."""
    ensure_pg_schema()
    event_id = UUID("00000000-0000-4000-8000-000000000301")
    aggregate_id = UUID("00000000-0000-4000-8000-000000000302")
    event_type = "test.outbox.commit.20260803"

    try:
        monkeypatch.setattr(outbox_repo, "uuid4", lambda: event_id)
        with _real_connection() as (writer_conn, writer_cur):
            with _real_connection() as (observer_conn, observer_cur):
                returned_id = enqueue_event(
                    writer_cur,
                    event_type,
                    "company",
                    str(aggregate_id),
                    {"company_id": str(aggregate_id), "source": "test"},
                )

                assert returned_id == str(event_id)
                writer_cur.execute(
                    "SELECT payload FROM outbox_events WHERE event_id = %s", (returned_id,)
                )
                assert writer_cur.fetchone() == (
                    {"company_id": str(aggregate_id), "source": "test"},
                )

                observer_cur.execute(
                    "SELECT COUNT(*) FROM outbox_events WHERE event_id = %s", (returned_id,)
                )
                assert observer_cur.fetchone() == (0,)

                writer_conn.commit()
                observer_conn.rollback()
                observer_cur.execute(
                    "SELECT COUNT(*) FROM outbox_events WHERE event_id = %s", (returned_id,)
                )
                assert observer_cur.fetchone() == (1,)
    finally:
        _delete_event(str(event_id))


def test_enqueue_event_lets_caller_rollback_the_real_postgres_transaction(monkeypatch):
    """An event must disappear when the transaction that supplied its cursor rolls back."""
    ensure_pg_schema()
    event_id = UUID("00000000-0000-4000-8000-000000000303")
    aggregate_id = UUID("00000000-0000-4000-8000-000000000304")

    try:
        monkeypatch.setattr(outbox_repo, "uuid4", lambda: event_id)
        with _real_connection() as (writer_conn, writer_cur):
            returned_id = enqueue_event(
                writer_cur,
                "test.outbox.rollback.20260803",
                "company",
                str(aggregate_id),
                {"company_id": str(aggregate_id)},
            )
            writer_cur.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE event_id = %s", (returned_id,)
            )
            assert writer_cur.fetchone() == (1,)
            writer_conn.rollback()

        with _real_connection() as (_, observer_cur):
            observer_cur.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE event_id = %s", (returned_id,)
            )
            assert observer_cur.fetchone() == (0,)
    finally:
        _delete_event(str(event_id))


def test_claim_events_leases_only_the_due_event_for_the_worker(monkeypatch):
    """Removing row locking or lease updates would leave the event claimable by another worker."""
    ensure_pg_schema()
    event_id = UUID("00000000-0000-4000-8000-000000000311")
    aggregate_id = UUID("00000000-0000-4000-8000-000000000312")

    try:
        _enqueue_committed(
            monkeypatch,
            event_id,
            "test.outbox.claim.20260803",
            aggregate_id,
            {"company_id": str(aggregate_id)},
        )

        claimed = claim_events("test-claim-worker", batch_size=1, lease_seconds=60)

        assert [event["event_id"] for event in claimed] == [str(event_id)]
        assert claimed[0]["payload"] == {"company_id": str(aggregate_id)}
        with get_cursor() as (_, cur):
            cur.execute(
                """
                SELECT locked_by, locked_until > NOW()
                FROM outbox_events
                WHERE event_id = %s
                """,
                (str(event_id),),
            )
            assert cur.fetchone() == ("test-claim-worker", True)
    finally:
        _delete_event(str(event_id))


def test_claim_events_skips_a_row_locked_by_another_real_postgres_connection(monkeypatch):
    """Replacing SKIP LOCKED with a blocking or unlocked claim breaks concurrent workers."""
    ensure_pg_schema()
    locked_event_id = UUID("00000000-0000-4000-8000-000000000401")
    available_event_id = UUID("00000000-0000-4000-8000-000000000402")
    locked_aggregate_id = UUID("00000000-0000-4000-8000-000000000403")
    available_aggregate_id = UUID("00000000-0000-4000-8000-000000000404")

    try:
        _enqueue_committed(
            monkeypatch,
            locked_event_id,
            "test.outbox.concurrent.locked.20260803",
            locked_aggregate_id,
            {"company_id": str(locked_aggregate_id)},
        )
        _enqueue_committed(
            monkeypatch,
            available_event_id,
            "test.outbox.concurrent.available.20260803",
            available_aggregate_id,
            {"company_id": str(available_aggregate_id)},
        )
        with get_cursor() as (_, cur):
            cur.execute(
                "UPDATE outbox_events SET next_attempt_at = %s WHERE event_id = %s",
                (datetime(2000, 1, 1, tzinfo=timezone.utc), str(locked_event_id)),
            )
            cur.execute(
                "UPDATE outbox_events SET next_attempt_at = %s WHERE event_id = %s",
                (datetime(2000, 1, 2, tzinfo=timezone.utc), str(available_event_id)),
            )

        with _real_connection() as (_, lock_cur):
            lock_cur.execute(
                "SELECT event_id FROM outbox_events WHERE event_id = %s FOR UPDATE",
                (str(locked_event_id),),
            )
            assert lock_cur.fetchone() == (str(locked_event_id),)

            claimed = claim_events("test-concurrent-worker-b", 1, 60)

            assert [event["event_id"] for event in claimed] == [str(available_event_id)]
            with get_cursor() as (_, cur):
                cur.execute(
                    """
                    SELECT event_id, locked_by
                    FROM outbox_events
                    WHERE event_id = ANY(%s::uuid[])
                    ORDER BY event_id
                    """,
                    ([str(locked_event_id), str(available_event_id)],),
                )
                assert cur.fetchall() == [
                    (str(locked_event_id), None),
                    (str(available_event_id), "test-concurrent-worker-b"),
                ]
    finally:
        _delete_event(str(locked_event_id))
        _delete_event(str(available_event_id))


def test_stale_worker_cannot_publish_or_fail_an_event_reclaimed_by_another_worker(monkeypatch):
    """An ownership-blind state update lets a stale worker overwrite a newer lease."""
    ensure_pg_schema()
    event_id = UUID("00000000-0000-4000-8000-000000000411")
    aggregate_id = UUID("00000000-0000-4000-8000-000000000412")
    event_type = "test.outbox.stale.20260803"
    consumer_name = "test_outbox_stale_consumer_20260803"
    handler_calls: list[str] = []

    def handler(event: dict) -> None:
        handler_calls.append(event["event_id"])
        with get_cursor() as (_, cur):
            cur.execute(
                """
                UPDATE outbox_events
                SET locked_until = NOW() - INTERVAL '1 second'
                WHERE event_id = %s
                """,
                (event["event_id"],),
            )
        reclaimed = claim_events("test-stale-worker-b", 1, 60)
        assert [claimed_event["event_id"] for claimed_event in reclaimed] == [event["event_id"]]

    try:
        register_consumer(event_type, consumer_name, handler)
        _enqueue_committed(
            monkeypatch,
            event_id,
            event_type,
            aggregate_id,
            {"company_id": str(aggregate_id)},
        )

        result = process_outbox_batch("test-stale-worker-a", 1, 3, 60)

        assert result == {"claimed": 1, "published": 0, "failed": 0}
        assert handler_calls == [str(event_id)]
        with get_cursor() as (_, cur):
            cur.execute(
                """
                SELECT published_at, dead_lettered_at, attempt_count, locked_by,
                       locked_until > NOW()
                FROM outbox_events
                WHERE event_id = %s
                """,
                (str(event_id),),
            )
            assert cur.fetchone() == (None, None, 0, "test-stale-worker-b", True)

        assert mark_published(str(event_id), "test-stale-worker-a") is False
        assert mark_failed(
            str(event_id), "stale failure", 3, 2, "test-stale-worker-a"
        ) is False
        with get_cursor() as (_, cur):
            cur.execute(
                """
                SELECT published_at, dead_lettered_at, attempt_count, last_error, locked_by
                FROM outbox_events
                WHERE event_id = %s
                """,
                (str(event_id),),
            )
            assert cur.fetchone() == (None, None, 0, None, "test-stale-worker-b")
    finally:
        _delete_event(str(event_id))


def test_process_outbox_batch_records_success_and_skips_repeated_delivery(monkeypatch):
    """Dropping consumption persistence would make a re-delivered event invoke its handler twice."""
    ensure_pg_schema()
    event_id = UUID("00000000-0000-4000-8000-000000000321")
    aggregate_id = UUID("00000000-0000-4000-8000-000000000322")
    event_type = "test.outbox.success.20260803"
    consumer_name = "test_outbox_success_consumer_20260803"
    observed_event_ids: list[str] = []

    def handler(event: dict) -> None:
        observed_event_ids.append(event["event_id"])

    try:
        register_consumer(event_type, consumer_name, handler)
        _enqueue_committed(
            monkeypatch,
            event_id,
            event_type,
            aggregate_id,
            {"company_id": str(aggregate_id)},
        )

        first_result = process_outbox_batch("test-success-worker", 10, 3, 60)
        with get_cursor() as (_, cur):
            cur.execute(
                """
                UPDATE outbox_events
                SET published_at = NULL, locked_by = NULL, locked_until = NULL
                WHERE event_id = %s
                """,
                (str(event_id),),
            )
        second_result = process_outbox_batch("test-success-worker", 10, 3, 60)

        assert first_result == {"claimed": 1, "published": 1, "failed": 0}
        assert second_result == {"claimed": 1, "published": 1, "failed": 0}
        assert observed_event_ids == [str(event_id)]
        with get_cursor() as (_, cur):
            cur.execute(
                """
                SELECT published_at IS NOT NULL, COUNT(outbox_consumptions.event_id)
                FROM outbox_events
                LEFT JOIN outbox_consumptions USING (event_id)
                WHERE event_id = %s
                GROUP BY published_at
                """,
                (str(event_id),),
            )
            assert cur.fetchone() == (True, 1)
    finally:
        _delete_event(str(event_id))


def test_process_outbox_batch_does_not_claim_new_work_when_rollout_is_frozen(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ROLLOUT_STATE", "rollback_frozen")
    monkeypatch.setattr(
        outbox_service.repo,
        "claim_events",
        lambda *_: pytest.fail("rollback must stop new leases before claim"),
    )

    assert process_outbox_batch("frozen-worker", 1, 3, 60) == {
        "claimed": 0,
        "published": 0,
        "failed": 0,
        "status": "rollback_frozen",
    }


def test_outbox_consumption_unique_key_rejects_duplicate_delivery_record(monkeypatch):
    """Dropping the schema's consumer key would let one event be recorded as consumed twice."""
    ensure_pg_schema()
    event_id = UUID("00000000-0000-4000-8000-000000000371")
    aggregate_id = UUID("00000000-0000-4000-8000-000000000372")
    consumer_name = "test_outbox_unique_consumer_20260812"

    try:
        _enqueue_committed(
            monkeypatch,
            event_id,
            "test.outbox.consumption-unique.20260812",
            aggregate_id,
            {"company_id": str(aggregate_id)},
        )

        assert record_consumption(str(event_id), consumer_name) is True
        assert record_consumption(str(event_id), consumer_name) is False
        with get_cursor() as (_, cur):
            cur.execute(
                "SELECT COUNT(*) FROM outbox_consumptions WHERE event_id = %s AND consumer_name = %s",
                (str(event_id), consumer_name),
            )
            assert cur.fetchone() == (1,)
    finally:
        _delete_event(str(event_id))


def test_v2_action_event_dead_letters_after_five_real_repository_attempts(monkeypatch):
    """Using the global retry maximum would leave V2 actions retriable after their fifth failed delivery."""
    ensure_pg_schema()
    event_id = UUID("00000000-0000-4000-8000-000000000381")
    aggregate_id = UUID("00000000-0000-4000-8000-000000000382")
    event_type = "agent.action.approved"
    consumer_name = "sourcing_risk_action"
    attempts: list[str] = []

    def fail_handler(event: dict) -> None:
        attempts.append(event["event_id"])
        raise RuntimeError("planned V2 failure")

    try:
        monkeypatch.setitem(
            outbox_service._CONSUMERS,
            (event_type, consumer_name),
            fail_handler,
        )
        _enqueue_committed(
            monkeypatch,
            event_id,
            event_type,
            aggregate_id,
            {"run_id": str(aggregate_id), "proposal_id": str(aggregate_id)},
        )

        for attempt in range(1, 6):
            assert process_outbox_batch("test-v2-worker", 1, 99, 60) == {
                "claimed": 1,
                "published": 0,
                "failed": 1,
            }
            with get_cursor() as (_, cur):
                cur.execute(
                    "SELECT attempt_count, dead_lettered_at IS NOT NULL FROM outbox_events WHERE event_id = %s",
                    (str(event_id),),
                )
                assert cur.fetchone() == (attempt, attempt == 5)
                if attempt < 5:
                    cur.execute(
                        "UPDATE outbox_events SET next_attempt_at = NOW() WHERE event_id = %s",
                        (str(event_id),),
                    )

        assert attempts == [str(event_id)] * 5
    finally:
        _delete_event(str(event_id))


def test_process_outbox_batch_retries_dead_letters_and_replays_with_real_handler(monkeypatch):
    """Wrong retry, dead-letter, replay, or consumption state leaves observable delivery state incorrect."""
    ensure_pg_schema()
    event_id = UUID("00000000-0000-4000-8000-000000000331")
    aggregate_id = UUID("00000000-0000-4000-8000-000000000332")
    event_type = "test.outbox.retry.20260803"
    consumer_name = "test_outbox_retry_consumer_20260803"
    handler_state = {"attempts": 0, "handled": []}

    def handler(event: dict) -> None:
        handler_state["attempts"] += 1
        if handler_state["attempts"] < 3:
            raise RuntimeError("planned retry failure")
        handler_state["handled"].append(event["event_id"])

    try:
        register_consumer(event_type, consumer_name, handler)
        _enqueue_committed(
            monkeypatch,
            event_id,
            event_type,
            aggregate_id,
            {"company_id": str(aggregate_id)},
        )

        failure_started_at = datetime.now(timezone.utc)
        first_result = process_outbox_batch("test-retry-worker", 10, 2, 60)
        assert first_result == {"claimed": 1, "published": 0, "failed": 1}
        with get_cursor() as (_, cur):
            cur.execute(
                """
                SELECT attempt_count, last_error, dead_lettered_at, next_attempt_at
                FROM outbox_events
                WHERE event_id = %s
                """,
                (str(event_id),),
            )
            attempt_count, last_error, dead_lettered_at, next_attempt_at = cur.fetchone()
            assert attempt_count == 1
            assert last_error == "consumer_handler_failed"
            assert dead_lettered_at is None
            assert next_attempt_at >= failure_started_at + timedelta(seconds=2)
            assert next_attempt_at <= datetime.now(timezone.utc) + timedelta(seconds=2)
            cur.execute(
                "UPDATE outbox_events SET next_attempt_at = NOW() WHERE event_id = %s",
                (str(event_id),),
            )

        second_result = process_outbox_batch("test-retry-worker", 10, 2, 60)
        assert second_result == {"claimed": 1, "published": 0, "failed": 1}
        dead_lettered = list_events("dead_lettered", 10)
        assert str(event_id) in [event["event_id"] for event in dead_lettered]

        replayed = replay_event(str(event_id), "恢复测试事件", None)
        assert replayed == {
            "event_id": str(event_id),
            "status": "queued",
            "reason": "恢复测试事件",
        }
        assert str(event_id) in [event["event_id"] for event in list_events("pending", 10)]
        assert str(event_id) not in [event["event_id"] for event in list_events("failed", 10)]
        with get_cursor() as (_, cur):
            cur.execute(
                """
                SELECT attempt_count, last_error, dead_lettered_at, published_at,
                       locked_by, locked_until
                FROM outbox_events
                WHERE event_id = %s
                """,
                (str(event_id),),
            )
            assert cur.fetchone() == (2, None, None, None, None, None)

        final_result = process_outbox_batch("test-retry-worker", 10, 2, 60)
        assert final_result == {"claimed": 1, "published": 1, "failed": 0}
        assert handler_state == {"attempts": 3, "handled": [str(event_id)]}
    finally:
        _delete_event(str(event_id))


def test_replay_preserves_successful_consumer_and_retries_only_failed_consumer(monkeypatch):
    """Clearing prior consumption during replay would invoke a successful consumer again."""
    ensure_pg_schema()
    event_id = UUID("00000000-0000-4000-8000-000000000421")
    aggregate_id = UUID("00000000-0000-4000-8000-000000000422")
    event_type = "test.outbox.partial.20260803"
    successful_consumer = "test_outbox_partial_success_20260803"
    retrying_consumer = "test_outbox_partial_retry_20260803"
    successful_calls: list[str] = []
    retry_state = {"attempts": 0, "successful_calls": []}

    def successful_handler(event: dict) -> None:
        successful_calls.append(event["event_id"])

    def retrying_handler(event: dict) -> None:
        retry_state["attempts"] += 1
        if retry_state["attempts"] == 1:
            raise RuntimeError("planned partial consumer failure")
        retry_state["successful_calls"].append(event["event_id"])

    try:
        register_consumer(event_type, successful_consumer, successful_handler)
        register_consumer(event_type, retrying_consumer, retrying_handler)
        _enqueue_committed(
            monkeypatch,
            event_id,
            event_type,
            aggregate_id,
            {"company_id": str(aggregate_id)},
        )

        first_result = process_outbox_batch("test-partial-worker", 1, 1, 60)
        assert first_result == {"claimed": 1, "published": 0, "failed": 1}
        with get_cursor() as (_, cur):
            cur.execute(
                """
                SELECT consumer_name
                FROM outbox_consumptions
                WHERE event_id = %s
                ORDER BY consumer_name
                """,
                (str(event_id),),
            )
            assert cur.fetchall() == [(successful_consumer,)]

        assert replay_event(str(event_id), "重放部分成功事件", None) == {
            "event_id": str(event_id),
            "status": "queued",
            "reason": "重放部分成功事件",
        }
        final_result = process_outbox_batch("test-partial-worker", 1, 1, 60)

        assert final_result == {"claimed": 1, "published": 1, "failed": 0}
        assert successful_calls == [str(event_id)]
        assert retry_state == {
            "attempts": 2,
            "successful_calls": [str(event_id)],
        }
        with get_cursor() as (_, cur):
            cur.execute(
                """
                SELECT published_at IS NOT NULL, array_agg(consumer_name ORDER BY consumer_name)
                FROM outbox_events
                JOIN outbox_consumptions USING (event_id)
                WHERE event_id = %s
                GROUP BY published_at
                """,
                (str(event_id),),
            )
            assert cur.fetchone() == (
                True,
                [retrying_consumer, successful_consumer],
            )
    finally:
        _delete_event(str(event_id))


def test_replay_rejects_an_event_actively_claimed_by_a_real_worker(monkeypatch):
    """Clearing a live lease would let an admin replay race a worker and duplicate delivery."""
    ensure_pg_schema()
    event_id = UUID("00000000-0000-4000-8000-000000000441")
    aggregate_id = UUID("00000000-0000-4000-8000-000000000442")
    event_type = "test.outbox.active-lease.20260808"
    consumer_name = "test_outbox_active_lease_consumer_20260808"
    handler_started = Event()
    release_handler = Event()
    handler_calls: list[str] = []
    worker_results: list[dict] = []

    def handler(event: dict) -> None:
        handler_calls.append(event["event_id"])
        handler_started.set()
        assert release_handler.wait(timeout=5)

    worker_thread: Thread | None = None
    try:
        register_consumer(event_type, consumer_name, handler)
        _enqueue_committed(
            monkeypatch,
            event_id,
            event_type,
            aggregate_id,
            {"company_id": str(aggregate_id)},
        )
        with get_cursor() as (_, cur):
            cur.execute(
                """
                UPDATE outbox_events
                SET attempt_count = 1, last_error = %s, next_attempt_at = NOW()
                WHERE event_id = %s
                """,
                ("prior delivery failure", str(event_id)),
            )

        worker_thread = Thread(
            target=lambda: worker_results.append(
                process_outbox_batch("test-active-lease-worker", 1, 3, 60)
            )
        )
        worker_thread.start()
        assert handler_started.wait(timeout=5)

        with pytest.raises(DomainError) as exc_info:
            replay_event(str(event_id), "不得抢占活动租约", None)
        assert exc_info.value.code == "OUTBOX_EVENT_LEASE_ACTIVE"
        assert exc_info.value.status_code == 409

        with get_cursor() as (_, cur):
            cur.execute(
                """
                SELECT locked_by, locked_until > NOW(), last_error, attempt_count,
                       dead_lettered_at, published_at
                FROM outbox_events WHERE event_id = %s
                """,
                (str(event_id),),
            )
            assert cur.fetchone() == (
                "test-active-lease-worker",
                True,
                "prior delivery failure",
                1,
                None,
                None,
            )

        release_handler.set()
        worker_thread.join(timeout=5)
        assert not worker_thread.is_alive()
        assert worker_results == [{"claimed": 1, "published": 1, "failed": 0}]
        assert handler_calls == [str(event_id)]
    finally:
        release_handler.set()
        if worker_thread is not None:
            worker_thread.join(timeout=5)
        _delete_event(str(event_id))


@pytest.mark.parametrize("expired_lease", (False, True))
def test_replay_allows_failed_event_with_missing_or_expired_lease(
    monkeypatch: pytest.MonkeyPatch,
    expired_lease: bool,
) -> None:
    """Treating every retained claim as active would strand recoverable failed events."""
    ensure_pg_schema()
    event_id = UUID("00000000-0000-4000-8000-000000000451")
    aggregate_id = UUID("00000000-0000-4000-8000-000000000452")

    try:
        _enqueue_committed(
            monkeypatch,
            event_id,
            "test.outbox.expired-lease.20260808",
            aggregate_id,
            {"company_id": str(aggregate_id)},
        )
        with get_cursor() as (_, cur):
            if expired_lease:
                cur.execute(
                    """
                    UPDATE outbox_events
                    SET attempt_count = 2,
                        last_error = %s,
                        locked_by = %s,
                        locked_until = NOW() - INTERVAL '1 second'
                    WHERE event_id = %s
                    """,
                    ("recoverable failure", "expired-worker", str(event_id)),
                )
            else:
                cur.execute(
                    """
                    UPDATE outbox_events
                    SET attempt_count = 2, last_error = %s
                    WHERE event_id = %s
                    """,
                    ("recoverable failure", str(event_id)),
                )

        assert replay_event(str(event_id), "恢复过期租约事件", None) == {
            "event_id": str(event_id),
            "status": "queued",
            "reason": "恢复过期租约事件",
        }
        with get_cursor() as (_, cur):
            cur.execute(
                """
                SELECT attempt_count, last_error, locked_by, locked_until, dead_lettered_at
                FROM outbox_events WHERE event_id = %s
                """,
                (str(event_id),),
            )
            assert cur.fetchone() == (2, None, None, None, None)
    finally:
        _delete_event(str(event_id))


def test_worker_and_service_sanitize_and_bound_delivery_errors(monkeypatch):
    """Persisting raw exception text would leak secrets and unbounded data to admin operations."""
    ensure_pg_schema()
    event_id = UUID("00000000-0000-4000-8000-000000000461")
    aggregate_id = UUID("00000000-0000-4000-8000-000000000462")
    event_type = "test.outbox.sanitized-error.20260808"
    consumer_name = "test_outbox_sanitized_error_consumer_20260808"
    raw_error = "token=super-secret\npassword: hunter2\x00" + ("x" * 500)

    def handler(event: dict) -> None:
        raise RuntimeError(raw_error)

    try:
        register_consumer(event_type, consumer_name, handler)
        _enqueue_committed(
            monkeypatch,
            event_id,
            event_type,
            aggregate_id,
            {"company_id": str(aggregate_id)},
        )

        assert process_outbox_batch("test-sanitized-error-worker", 1, 3, 60) == {
            "claimed": 1,
            "published": 0,
            "failed": 1,
        }
        with get_cursor() as (_, cur):
            cur.execute(
                "SELECT last_error FROM outbox_events WHERE event_id = %s",
                (str(event_id),),
            )
            persisted_error = cur.fetchone()[0]
        assert persisted_error == "consumer_handler_failed"
        assert len(persisted_error) <= 240
        assert "super-secret" not in persisted_error
        assert "hunter2" not in persisted_error
        assert "\n" not in persisted_error
        assert "\x00" not in persisted_error

        listed_event = next(
            event for event in list_events("failed", 10) if event["event_id"] == str(event_id)
        )
        assert listed_event["last_error"] == persisted_error
    finally:
        _delete_event(str(event_id))


@pytest.mark.parametrize("attempt,seconds", [(1, 2), (7, 128), (9, 300)])
def test_retry_delay_is_exponential_and_capped(attempt, seconds):
    """Changing either the backoff base or cap must alter the literal next-delay contract."""
    assert retry_delay_seconds(attempt) == seconds


@pytest.mark.parametrize(
    "legacy_error",
    [
        "postgres://user:password@example.invalid/db?token=super-secret",
        '{"token":"super-secret","nested":{"authorization":"Bearer jwt-value"}}',
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
    ],
)
def test_admin_projection_maps_legacy_errors_to_generic_allowlisted_code(legacy_error: str):
    """Historical failure text is never trusted as safe output, even after regex-style redaction."""
    from app.domains.outbox.service import to_admin_event

    assert to_admin_event(
        {
            "event_id": "00000000-0000-4000-8000-000000000886",
            "event_type": "company.created",
            "aggregate_type": "company",
            "aggregate_id": "00000000-0000-4000-8000-000000000887",
            "schema_version": 1,
            "attempt_count": 1,
            "occurred_at": datetime.now(timezone.utc),
            "published_at": None,
            "last_error": legacy_error,
        }
    )["last_error"] == "delivery_failed"


def test_replay_event_rejects_blank_reason_before_opening_a_transaction(monkeypatch):
    """Service callers bypassing HTTP validation must not create a blank replay audit record."""
    from contextlib import contextmanager

    from app.core.errors import DomainError
    from app.domains.outbox import service as outbox_service

    @contextmanager
    def unexpected_transaction():
        raise AssertionError("invalid replay reason must fail before database access")
        yield

    monkeypatch.setattr(outbox_service, "get_cursor", unexpected_transaction)

    with pytest.raises(DomainError) as exc_info:
        outbox_service.replay_event(
            "00000000-0000-4000-8000-000000000885",
            " \t ",
            None,
        )

    assert exc_info.value.code == "OUTBOX_REPLAY_REASON_INVALID"


def test_process_outbox_batch_retries_unknown_event_type_without_publishing(monkeypatch):
    """An event without a registered consumer is undeliverable and must remain retryable."""
    from app.domains.outbox import service as outbox_service

    event_id = "00000000-0000-4000-8000-000000000881"
    _enqueue_committed(
        monkeypatch,
        event_id,
        "test.outbox.unregistered-consumer.20260808",
        "00000000-0000-4000-8000-000000000882",
        {"test": "unknown-consumer"},
    )
    try:
        monkeypatch.setattr(outbox_service, "_CONSUMERS", {})
        monkeypatch.setattr(outbox_service, "retry_delay_seconds", lambda attempt: 0)

        result = process_outbox_batch("test-unknown-consumer-worker", 1, 2, 60)

        assert result == {"claimed": 1, "published": 0, "failed": 1}
        with _real_connection() as (_, cur):
            cur.execute(
                """
                SELECT published_at, dead_lettered_at, attempt_count, last_error
                FROM outbox_events
                WHERE event_id = %s
                """,
                (event_id,),
            )
            published_at, dead_lettered_at, attempt_count, last_error = cur.fetchone()
        assert published_at is None
        assert dead_lettered_at is None
        assert attempt_count == 1
        assert last_error == "consumer_not_registered"

        result = process_outbox_batch("test-unknown-consumer-worker", 1, 2, 60)
        assert result == {"claimed": 1, "published": 0, "failed": 1}
        with _real_connection() as (_, cur):
            cur.execute(
                """
                SELECT published_at, dead_lettered_at, attempt_count, last_error
                FROM outbox_events
                WHERE event_id = %s
                """,
                (event_id,),
            )
            published_at, dead_lettered_at, attempt_count, last_error = cur.fetchone()
        assert published_at is None
        assert dead_lettered_at is not None
        assert attempt_count == 2
        assert last_error == "consumer_not_registered"
    finally:
        _delete_event(event_id)


def test_process_outbox_batch_persists_only_allowlisted_error_diagnostic(monkeypatch):
    """Raw exception messages can contain credentials and must never be retained in Outbox rows."""
    from app.domains.outbox import service as outbox_service

    event_type = "test.outbox.allowlisted-error.20260808"
    event_id = "00000000-0000-4000-8000-000000000883"
    _enqueue_committed(
        monkeypatch,
        event_id,
        event_type,
        "00000000-0000-4000-8000-000000000884",
        {"test": "safe-error"},
    )

    def raise_sensitive_error(event: dict) -> None:
        raise RuntimeError("postgres://user:secret@example.invalid/db?token=do-not-persist")

    try:
        monkeypatch.setattr(
            outbox_service,
            "_CONSUMERS",
            {(event_type, "test-sensitive-error"): raise_sensitive_error},
        )

        assert process_outbox_batch("test-safe-error-worker", 1, 3, 60) == {
            "claimed": 1,
            "published": 0,
            "failed": 1,
        }

        with _real_connection() as (_, cur):
            cur.execute(
                "SELECT last_error FROM outbox_events WHERE event_id = %s",
                (event_id,),
            )
            persisted_error = cur.fetchone()[0]
        assert persisted_error == "consumer_handler_failed"
        assert "secret" not in persisted_error
        assert "do-not-persist" not in persisted_error
    finally:
        _delete_event(event_id)


def test_register_consumer_rejects_a_different_handler_for_the_same_key():
    """Overwriting a registered consumer would make delivery behavior depend on import order."""
    event_type = "test.outbox.registration.20260803"
    consumer_name = "test_outbox_registration_consumer_20260803"

    def first_handler(event: dict) -> None:
        return None

    def second_handler(event: dict) -> None:
        return None

    register_consumer(event_type, consumer_name, first_handler)
    register_consumer(event_type, consumer_name, first_handler)
    with pytest.raises(ValueError, match="消费者已注册"):
        register_consumer(event_type, consumer_name, second_handler)


def test_v2_action_dispatch_does_not_run_an_additional_consumer(monkeypatch):
    """V2 approved actions have one business consumer, not legacy-style fan-out."""
    event = {
        "event_id": "event-v2-routing",
        "event_type": "agent.action.approved",
        "attempt_count": 0,
        "payload": {},
    }
    calls: list[str] = []

    def action_handler(_: dict) -> None:
        calls.append("action")

    def unrelated_handler(_: dict) -> None:
        calls.append("unrelated")
        raise RuntimeError("must not intercept V2 action")

    monkeypatch.setattr(outbox_service.repo, "claim_events", lambda *_: [event])
    monkeypatch.setattr(outbox_service.repo, "is_consumed", lambda *_: False)
    monkeypatch.setattr(outbox_service.repo, "record_consumption", lambda *_: True)
    monkeypatch.setattr(outbox_service.repo, "mark_published", lambda *_: True)
    monkeypatch.setattr(outbox_service.repo, "mark_failed", lambda *args: False)
    monkeypatch.setattr(
        outbox_service,
        "_CONSUMERS",
        {
            ("agent.action.approved", "sourcing_risk_action"): action_handler,
            ("agent.action.approved", "unrelated_consumer"): unrelated_handler,
        },
    )

    assert outbox_service.process_outbox_batch("worker", 1, 99, 60) == {
        "claimed": 1,
        "published": 1,
        "failed": 0,
    }
    assert calls == ["action"]
