"""Transactional PostgreSQL coverage for Company Identity commands."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import pytest
from psycopg2 import sql
from psycopg2.errors import CheckViolation

from app.db import init_pg
from app.db.postgres import get_conn, put_conn
from app.domains.company import service as company_service
from app.schemas.company import CompanyAliasInput, CompanyCreateInput


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
