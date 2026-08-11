"""Real PostgreSQL tests for P1 company facts and transactional Outbox events."""

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest

from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_cursor
from app.domains.company import service as company_service
from app.schemas.company import (
    CompanyCreateInput,
    CompanyMergeInput,
    CompanyUpdateInput,
    CompanyVerifyInput,
)


@pytest.fixture
def pg_cursor() -> Iterator[Any]:
    """Provide a cursor for assertions after command transactions have committed."""
    ensure_pg_schema()
    with get_cursor() as (_, cur):
        yield cur


@pytest.fixture
def tracked_company_ids() -> Iterator[list[str]]:
    """Delete only records created by this test, never shared PostgreSQL data."""
    company_ids: list[str] = []
    yield company_ids
    if not company_ids:
        return

    with get_cursor() as (_, cur):
        cur.execute(
            """
            DELETE FROM outbox_consumptions
            WHERE event_id IN (
                SELECT event_id FROM outbox_events WHERE aggregate_id = ANY(%s::uuid[])
            )
            """,
            (company_ids,),
        )
        cur.execute(
            "DELETE FROM outbox_events WHERE aggregate_id = ANY(%s::uuid[])",
            (company_ids,),
        )
        cur.execute(
            "DELETE FROM audit_logs WHERE resource_id = ANY(%s)",
            (company_ids,),
        )
        cur.execute(
            """
            DELETE FROM company_merge_log
            WHERE source_company_id = ANY(%s::uuid[]) OR target_company_id = ANY(%s::uuid[])
            """,
            (company_ids, company_ids),
        )
        cur.execute("DELETE FROM company_aliases WHERE company_id = ANY(%s::uuid[])", (company_ids,))
        cur.execute("DELETE FROM companies WHERE id = ANY(%s::uuid[])", (company_ids,))


def _event_rows(pg_cursor: Any, company_ids: list[str]) -> list[tuple[str, str, dict]]:
    pg_cursor.execute(
        """
        SELECT event_type, aggregate_id::text, payload
        FROM outbox_events
        WHERE aggregate_id = ANY(%s::uuid[])
        ORDER BY occurred_at, event_id
        """,
        (company_ids,),
    )
    return pg_cursor.fetchall()


def test_company_facts_and_all_p1_events_commit_atomically(
    pg_cursor: Any,
    tracked_company_ids: list[str],
) -> None:
    """A partial command transaction would leave a company fact without its matching event."""
    source_name = f"P1-Outbox-Source-{uuid4()}"
    target_name = f"P1-Outbox-Target-{uuid4()}"

    source = company_service.create_company(
        CompanyCreateInput(legal_name=source_name),
        None,
        "admin",
    )
    tracked_company_ids.append(source["company_id"])
    target = company_service.create_company(
        CompanyCreateInput(legal_name=target_name),
        None,
        "admin",
    )
    tracked_company_ids.append(target["company_id"])

    updated = company_service.update_company(
        source["company_id"],
        CompanyUpdateInput(expected_version=1, registration_status="存续"),
        None,
        "admin",
    )
    verified = company_service.verify_company(
        source["company_id"],
        CompanyVerifyInput(
            expected_version=updated["identity_version"],
            identity_source="admin_verified",
            source_reference=f"p1-proof-{uuid4()}",
        ),
        None,
        "admin",
    )
    merged = company_service.merge_company(
        source["company_id"],
        CompanyMergeInput(
            target_company_id=target["company_id"],
            source_expected_version=verified["identity_version"],
            target_expected_version=target["identity_version"],
            reason="P1 transactional Outbox integration merge",
            confirm=True,
        ),
        None,
        "admin",
    )

    pg_cursor.execute(
        """
        SELECT registration_status, verification_status, identity_version, merged_into_id::text
        FROM companies WHERE id = %s
        """,
        (source["company_id"],),
    )
    assert pg_cursor.fetchone() == ("存续", "verified", 4, target["company_id"])
    assert merged == {
        "source_company_id": source["company_id"],
        "target_company_id": target["company_id"],
        "source_version": 4,
        "target_version": 2,
        "merged": True,
    }
    assert _event_rows(pg_cursor, tracked_company_ids) == [
        (
            "company.created",
            source["company_id"],
            {
                "company_id": source["company_id"],
                "legal_name": source_name,
                "identity_version": 1,
                "verification_status": "pending_verification",
            },
        ),
        (
            "company.created",
            target["company_id"],
            {
                "company_id": target["company_id"],
                "legal_name": target_name,
                "identity_version": 1,
                "verification_status": "pending_verification",
            },
        ),
        (
            "company.updated",
            source["company_id"],
            {
                "company_id": source["company_id"],
                "legal_name": source_name,
                "identity_version": 2,
                "verification_status": "pending_verification",
            },
        ),
        (
            "company.verified",
            source["company_id"],
            {
                "company_id": source["company_id"],
                "legal_name": source_name,
                "identity_version": 3,
                "verification_status": "verified",
            },
        ),
        (
            "company.merged",
            source["company_id"],
            {
                "source_company_id": source["company_id"],
                "target_company_id": target["company_id"],
                "operator_id": None,
                "reason": "P1 transactional Outbox integration merge",
                "source_version": 4,
                "target_version": 2,
            },
        ),
    ]


def test_company_and_event_roll_back_together_when_event_write_fails(
    monkeypatch: pytest.MonkeyPatch,
    pg_cursor: Any,
) -> None:
    """Catching event insertion failures would commit an unusable company fact."""
    unique_name = f"P1-Outbox-Rollback-{uuid4()}"

    def fail_enqueue(*args: object, **kwargs: object) -> str:
        raise RuntimeError("event insert failed")

    monkeypatch.setattr(company_service, "enqueue_event", fail_enqueue)

    with pytest.raises(RuntimeError, match="event insert failed"):
        company_service.create_company(
            CompanyCreateInput(legal_name=unique_name),
            None,
            "admin",
        )

    pg_cursor.execute("SELECT COUNT(*) FROM companies WHERE legal_name = %s", (unique_name,))
    assert pg_cursor.fetchone() == (0,)
    pg_cursor.execute(
        """
        SELECT COUNT(*) FROM outbox_events
        WHERE payload->>'legal_name' = %s
        """,
        (unique_name,),
    )
    assert pg_cursor.fetchone() == (0,)
