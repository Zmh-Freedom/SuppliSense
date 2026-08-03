"""Real PostgreSQL coverage for transactional Outbox behavior."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import pytest

from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_conn, get_cursor, put_conn
from app.domains.outbox import repo as outbox_repo
from app.domains.outbox.repo import claim_events, enqueue_event
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

        first_result = process_outbox_batch("test-retry-worker", 10, 2, 60)
        assert first_result == {"claimed": 1, "published": 0, "failed": 1}
        with get_cursor() as (_, cur):
            cur.execute(
                """
                SELECT attempt_count, last_error, dead_lettered_at
                FROM outbox_events
                WHERE event_id = %s
                """,
                (str(event_id),),
            )
            attempt_count, last_error, dead_lettered_at = cur.fetchone()
            assert attempt_count == 1
            assert "planned retry failure" in last_error
            assert dead_lettered_at is None
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
            assert cur.fetchone() == (0, None, None, None, None, None)

        final_result = process_outbox_batch("test-retry-worker", 10, 2, 60)
        assert final_result == {"claimed": 1, "published": 1, "failed": 0}
        assert handler_state == {"attempts": 3, "handled": [str(event_id)]}
    finally:
        _delete_event(str(event_id))


@pytest.mark.parametrize("attempt,seconds", [(1, 2), (7, 128), (9, 300)])
def test_retry_delay_is_exponential_and_capped(attempt, seconds):
    """Changing either the backoff base or cap must alter the literal next-delay contract."""
    assert retry_delay_seconds(attempt) == seconds


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
