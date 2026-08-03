"""Transactional PostgreSQL coverage for Company Identity commands."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Thread
from time import sleep
from uuid import uuid4

import pytest
from psycopg2 import sql
from psycopg2.errors import CheckViolation

from app.core.errors import DomainError
from app.db import init_pg
from app.db.postgres import get_conn, put_conn
from app.domains.company import service as company_service
from app.schemas.company import (
    CompanyAliasInput,
    CompanyCreateInput,
    CompanyUpdateInput,
    CompanyVerifyInput,
)


@contextmanager
def _isolated_company_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Create an isolated real PostgreSQL schema for injected database constraint failures."""
    schema_name = f"task5_company_{uuid4().hex}"
    conn = get_conn()
    created = False
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        conn.commit()
        created = True

        @contextmanager
        def schema_cursor() -> Iterator[tuple[object, object]]:
            cur = conn.cursor()
            try:
                cur.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema_name)))
                yield conn, cur
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                cur.close()

        monkeypatch.setattr(init_pg, "get_cursor", schema_cursor)
        init_pg.ensure_pg_schema()
        monkeypatch.setattr(company_service, "get_cursor", schema_cursor)
        yield schema_cursor
    finally:
        conn.rollback()
        if created:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO public")
                cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name)))
            conn.commit()
        put_conn(conn)


@contextmanager
def _isolated_company_schema_connections(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Create an isolated schema whose cursors use separate real PostgreSQL connections."""
    schema_name = f"task5_company_race_{uuid4().hex}"
    setup_conn = get_conn()
    created = False
    try:
        setup_conn.rollback()
        with setup_conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        setup_conn.commit()
        created = True

        @contextmanager
        def schema_cursor() -> Iterator[tuple[object, object]]:
            conn = get_conn()
            cur = None
            try:
                conn.rollback()
                cur = conn.cursor()
                cur.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(schema_name)))
                yield conn, cur
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                if cur is not None:
                    cur.close()
                put_conn(conn)

        monkeypatch.setattr(init_pg, "get_cursor", schema_cursor)
        init_pg.ensure_pg_schema()
        monkeypatch.setattr(company_service, "get_cursor", schema_cursor)
        yield schema_cursor
    finally:
        setup_conn.rollback()
        if created:
            with setup_conn.cursor() as cur:
                cur.execute("SET search_path TO public")
                cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name)))
            setup_conn.commit()
        put_conn(setup_conn)


def test_create_rolls_back_company_alias_and_audit_when_real_event_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Committing before event insertion would leave a company or audit row after the database rejects the event."""
    with _isolated_company_schema(monkeypatch) as schema_cursor:
        with schema_cursor() as (_, cur):
            cur.execute(
                "ALTER TABLE outbox_events ADD CONSTRAINT task5_reject_created_event "
                "CHECK (event_type <> 'company.created')"
            )

        with pytest.raises(CheckViolation):
            company_service.create_company(
                CompanyCreateInput(
                    legal_name="Task5 Rolled Back Company",
                    aliases=[CompanyAliasInput(alias_name="Task5 Rollback Alias", alias_type="short_name")],
                ),
                None,
                "analyst",
            )

        with schema_cursor() as (_, cur):
            cur.execute("SELECT COUNT(*) FROM companies")
            assert cur.fetchone() == (0,)
            cur.execute("SELECT COUNT(*) FROM company_aliases")
            assert cur.fetchone() == (0,)
            cur.execute("SELECT COUNT(*) FROM audit_logs WHERE action = 'company.created'")
            assert cur.fetchone() == (0,)
            cur.execute("SELECT COUNT(*) FROM outbox_events")
            assert cur.fetchone() == (0,)


def test_create_maps_real_credit_code_unique_race_and_rolls_back_before_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent credit-code insert must become a domain conflict, not leave a partially-created company."""
    credit_code = "911100007109250324"
    competing_company_id = str(uuid4())
    lock_id = 51005
    with _isolated_company_schema_connections(monkeypatch) as schema_cursor:
        with schema_cursor() as (_, cur):
            cur.execute(
                """
                CREATE FUNCTION task5_wait_for_credit_race() RETURNS trigger AS $$
                BEGIN
                    IF NEW.legal_name = 'Task5 Credit Race Company' THEN
                        PERFORM pg_advisory_lock(51005);
                        PERFORM pg_advisory_unlock(51005);
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """
            )
            cur.execute(
                "CREATE TRIGGER task5_wait_for_credit_race BEFORE INSERT ON companies "
                "FOR EACH ROW EXECUTE FUNCTION task5_wait_for_credit_race()"
            )

        blocker_conn = get_conn()
        blocker_cur = blocker_conn.cursor()
        blocker_cur.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
        raised: list[BaseException] = []

        def create_racing_company() -> None:
            try:
                company_service.create_company(
                    CompanyCreateInput(
                        legal_name="Task5 Credit Race Company",
                        unified_social_credit_code=credit_code,
                    ),
                    None,
                    "analyst",
                )
            except BaseException as exc:
                raised.append(exc)

        thread = Thread(target=create_racing_company)
        thread.start()
        try:
            waiting = False
            for _ in range(100):
                observer_conn = get_conn()
                try:
                    with observer_conn.cursor() as observer_cur:
                        observer_cur.execute(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks "
                            "WHERE locktype = 'advisory' AND objid = %s AND NOT granted)",
                            (lock_id,),
                        )
                        waiting = observer_cur.fetchone()[0]
                    observer_conn.rollback()
                finally:
                    put_conn(observer_conn)
                if waiting:
                    break
                sleep(0.01)
            assert waiting is True

            with schema_cursor() as (_, cur):
                cur.execute(
                    """
                    INSERT INTO companies (
                        id, legal_name, normalized_name, unified_social_credit_code,
                        verification_status, identity_source
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        competing_company_id,
                        "Task5 Competing Credit Company",
                        "task5 competing credit company",
                        credit_code,
                        "pending_verification",
                        "manual",
                    ),
                )
        finally:
            blocker_cur.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
            blocker_conn.commit()
            blocker_cur.close()
            put_conn(blocker_conn)
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert len(raised) == 1
        assert isinstance(raised[0], DomainError)
        assert raised[0].code == "COMPANY_ALREADY_EXISTS"
        assert raised[0].detail == {"company_id": competing_company_id}
        with schema_cursor() as (_, cur):
            cur.execute("SELECT legal_name FROM companies ORDER BY legal_name")
            assert cur.fetchall() == [("Task5 Competing Credit Company",)]
            cur.execute("SELECT COUNT(*) FROM audit_logs")
            assert cur.fetchone() == (0,)
            cur.execute("SELECT COUNT(*) FROM outbox_events")
            assert cur.fetchone() == (0,)


def test_failed_update_after_row_delete_reports_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """A row deleted after the initial read must not be misreported as a version conflict."""
    with _isolated_company_schema(monkeypatch) as schema_cursor:
        company = company_service.create_company(
            CompanyCreateInput(legal_name="Task5 Delete During Update Company"),
            None,
            "analyst",
        )
        with schema_cursor() as (_, cur):
            cur.execute(
                """
                CREATE FUNCTION task5_delete_before_update() RETURNS trigger AS $$
                BEGIN
                    DELETE FROM companies WHERE id = OLD.id;
                    RETURN NULL;
                END;
                $$ LANGUAGE plpgsql
                """
            )
            cur.execute(
                "CREATE TRIGGER task5_delete_before_update BEFORE UPDATE ON companies "
                "FOR EACH ROW EXECUTE FUNCTION task5_delete_before_update()"
            )

        with pytest.raises(DomainError) as exc_info:
            company_service.update_company(
                company["company_id"],
                CompanyUpdateInput(expected_version=1, registration_status="active"),
                None,
                "admin",
            )

    assert exc_info.value.code == "COMPANY_NOT_FOUND"


@pytest.mark.parametrize(
    ("event_type", "operation"),
    [
        (
            "company.updated",
            lambda company_id: company_service.update_company(
                company_id,
                CompanyUpdateInput(expected_version=1, registration_status="active"),
                None,
                "admin",
            ),
        ),
        (
            "company.verified",
            lambda company_id: company_service.verify_company(
                company_id,
                CompanyVerifyInput(
                    expected_version=1,
                    identity_source="admin_verified",
                    source_reference="task5-rollback-evidence",
                ),
                None,
                "admin",
            ),
        ),
    ],
)
def test_update_and_verify_roll_back_when_real_event_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
    operation: object,
) -> None:
    """An event failure must preserve the original company state and its pre-existing audit/outbox rows."""
    with _isolated_company_schema(monkeypatch) as schema_cursor:
        company = company_service.create_company(
            CompanyCreateInput(legal_name=f"Task5 {event_type} Rollback Company"),
            None,
            "analyst",
        )
        with schema_cursor() as (_, cur):
            cur.execute(
                "ALTER TABLE outbox_events ADD CONSTRAINT task5_reject_command_event "
                "CHECK (event_type <> %s)",
                (event_type,),
            )

        with pytest.raises(CheckViolation):
            operation(company["company_id"])

        with schema_cursor() as (_, cur):
            cur.execute(
                "SELECT registration_status, verification_status, identity_version, verified_at "
                "FROM companies WHERE id = %s",
                (company["company_id"],),
            )
            assert cur.fetchone() == (None, "pending_verification", 1, None)
            cur.execute(
                "SELECT action FROM audit_logs WHERE resource_id = %s ORDER BY action",
                (company["company_id"],),
            )
            assert cur.fetchall() == [("company.created",)]
            cur.execute(
                "SELECT event_type FROM outbox_events WHERE aggregate_id = %s",
                (company["company_id"],),
            )
            assert cur.fetchall() == [("company.created",)]
