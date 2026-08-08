"""Isolated real-PostgreSQL coverage for the P1 company and outbox schema."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from psycopg2 import sql

from app.db import init_pg
from app.db.postgres import get_conn, put_conn


P1_TABLES = {
    "companies",
    "company_aliases",
    "company_merge_log",
    "outbox_events",
    "outbox_consumptions",
}


@contextmanager
def _isolated_schema(monkeypatch) -> Iterator[tuple[str, object]]:
    """Run the real initializer in a UUID-named schema and remove only it."""
    schema_name = f"task_2_schema_{uuid.uuid4().hex}"
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
                cur.execute(
                    sql.SQL("SET search_path TO {}, public").format(
                        sql.Identifier(schema_name)
                    )
                )
                yield conn, cur
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                cur.close()

        monkeypatch.setattr(init_pg, "get_cursor", schema_cursor)
        yield schema_name, conn
    finally:
        conn.rollback()
        if created:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO public")
                cur.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema_name)
                    )
                )
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema_name,)
                )
                assert cur.fetchone() is None
        put_conn(conn)


@contextmanager
def _schema_cursor(conn: object, schema_name: str) -> Iterator[object]:
    cur = conn.cursor()
    try:
        cur.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema_name))
        )
        yield cur
    finally:
        cur.close()


def _columns(cur: object, schema_name: str, table_name: str) -> dict[str, tuple[str, int | None, str]]:
    cur.execute(
        """
        SELECT column_name, data_type, character_maximum_length, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        """,
        (schema_name, table_name),
    )
    return {
        name: (data_type, maximum_length, nullable)
        for name, data_type, maximum_length, nullable in cur.fetchall()
    }


def _constraints(cur: object, schema_name: str, table_name: str) -> list[dict]:
    cur.execute(
        """
        SELECT
            constraint_row.conname,
            constraint_row.contype,
            constraint_row.columns,
            constraint_row.definition,
            constraint_row.referenced_table,
            constraint_row.confdeltype
        FROM (
            SELECT
                c.conname,
                c.contype,
                ARRAY(
                    SELECT a.attname
                    FROM unnest(c.conkey) WITH ORDINALITY AS key_column(attnum, ordinality)
                    JOIN pg_attribute AS a
                      ON a.attrelid = c.conrelid AND a.attnum = key_column.attnum
                    ORDER BY key_column.ordinality
                ) AS columns,
                pg_get_constraintdef(c.oid) AS definition,
                referenced_namespace.nspname || '.' || referenced_relation.relname AS referenced_table,
                c.confdeltype
            FROM pg_constraint AS c
            LEFT JOIN pg_class AS referenced_relation ON referenced_relation.oid = c.confrelid
            LEFT JOIN pg_namespace AS referenced_namespace
              ON referenced_namespace.oid = referenced_relation.relnamespace
            WHERE c.conrelid = (%s || '.' || %s)::regclass
        ) AS constraint_row
        ORDER BY constraint_row.conname
        """,
        (schema_name, table_name),
    )
    names = ["name", "type", "columns", "definition", "referenced_table", "delete_type"]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _indexes(cur: object, schema_name: str, table_name: str) -> dict[str, dict]:
    cur.execute(
        """
        SELECT
            index_relation.relname,
            ARRAY(
                SELECT a.attname
                FROM unnest(index_definition.indkey) WITH ORDINALITY AS key_column(attnum, ordinality)
                JOIN pg_attribute AS a
                  ON a.attrelid = index_definition.indrelid AND a.attnum = key_column.attnum
                WHERE key_column.attnum > 0
                ORDER BY key_column.ordinality
            ) AS keys,
            pg_get_expr(index_definition.indpred, index_definition.indrelid) AS predicate
        FROM pg_index AS index_definition
        JOIN pg_class AS table_relation ON table_relation.oid = index_definition.indrelid
        JOIN pg_namespace AS table_namespace ON table_namespace.oid = table_relation.relnamespace
        JOIN pg_class AS index_relation ON index_relation.oid = index_definition.indexrelid
        WHERE table_namespace.nspname = %s AND table_relation.relname = %s
        """,
        (schema_name, table_name),
    )
    return {
        name: {"keys": tuple(keys), "predicate": predicate}
        for name, keys, predicate in cur.fetchall()
    }


def _find_constraint(
    constraints: list[dict], constraint_type: str, columns: tuple[str, ...]
) -> dict:
    return next(
        constraint
        for constraint in constraints
        if constraint["type"] == constraint_type and tuple(constraint["columns"]) == columns
    )


def _assert_fk(
    constraints: list[dict],
    column: str,
    referenced_table: str,
    delete_type: str,
) -> None:
    foreign_key = _find_constraint(constraints, "f", (column,))
    assert foreign_key["referenced_table"] == referenced_table
    assert foreign_key["delete_type"] == delete_type


def _check_definitions(constraints: list[dict]) -> list[str]:
    return [constraint["definition"] for constraint in constraints if constraint["type"] == "c"]


def _assert_columns(
    actual: dict[str, tuple[str, int | None, str]],
    expected: dict[str, tuple[str, int | None, str]],
) -> None:
    assert actual == expected


def test_ensure_pg_schema_creates_complete_p1_contract_in_isolated_schema(monkeypatch):
    """Removing a P1 DDL statement or its catalog contract must fail in an empty schema."""
    with _isolated_schema(monkeypatch) as (schema_name, conn):
        init_pg.ensure_pg_schema()
        init_pg.ensure_pg_schema()

        with _schema_cursor(conn, schema_name) as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_name = ANY(%s)
                """,
                (schema_name, list(P1_TABLES)),
            )
            assert {row[0] for row in cur.fetchall()} == P1_TABLES

            _assert_columns(
                _columns(cur, schema_name, "companies"),
                {
                    "id": ("uuid", None, "NO"),
                    "legal_name": ("character varying", 255, "NO"),
                    "normalized_name": ("character varying", 255, "NO"),
                    "unified_social_credit_code": ("character varying", 18, "YES"),
                    "registration_status": ("character varying", 32, "YES"),
                    "verification_status": ("character varying", 24, "NO"),
                    "identity_source": ("character varying", 32, "NO"),
                    "source_reference": ("character varying", 255, "YES"),
                    "identity_version": ("integer", None, "NO"),
                    "merged_into_id": ("uuid", None, "YES"),
                    "created_by": ("uuid", None, "YES"),
                    "verified_by": ("uuid", None, "YES"),
                    "verified_at": ("timestamp with time zone", None, "YES"),
                    "created_at": ("timestamp with time zone", None, "NO"),
                    "updated_at": ("timestamp with time zone", None, "NO"),
                },
            )
            company_constraints = _constraints(cur, schema_name, "companies")
            _find_constraint(company_constraints, "p", ("id",))
            _find_constraint(company_constraints, "u", ("unified_social_credit_code",))
            _assert_fk(company_constraints, "merged_into_id", f"{schema_name}.companies", "a")
            _assert_fk(company_constraints, "created_by", f"{schema_name}.users", "n")
            _assert_fk(company_constraints, "verified_by", f"{schema_name}.users", "n")
            company_checks = _check_definitions(company_constraints)
            assert any("verified" in definition and "pending_verification" in definition for definition in company_checks)
            assert any(
                "unified_social_credit_code" in definition
                and "source_reference" in definition
                and "admin_verified" in definition
                for definition in company_checks
            )
            assert any("identity_version > 0" in definition for definition in company_checks)
            assert any("merged_into_id IS NULL" in definition and "merged_into_id <> id" in definition for definition in company_checks)

            _assert_columns(
                _columns(cur, schema_name, "company_aliases"),
                {
                    "id": ("uuid", None, "NO"),
                    "company_id": ("uuid", None, "NO"),
                    "alias_name": ("character varying", 255, "NO"),
                    "normalized_alias": ("character varying", 255, "NO"),
                    "alias_type": ("character varying", 24, "NO"),
                    "source": ("character varying", 32, "NO"),
                    "confidence": ("numeric", None, "NO"),
                    "created_by": ("uuid", None, "YES"),
                    "created_at": ("timestamp with time zone", None, "NO"),
                },
            )
            alias_constraints = _constraints(cur, schema_name, "company_aliases")
            _find_constraint(alias_constraints, "p", ("id",))
            _find_constraint(alias_constraints, "u", ("company_id", "normalized_alias"))
            _assert_fk(alias_constraints, "company_id", f"{schema_name}.companies", "a")
            _assert_fk(alias_constraints, "created_by", f"{schema_name}.users", "n")
            alias_checks = _check_definitions(alias_constraints)
            assert any("short_name" in definition and "source_name" in definition for definition in alias_checks)
            assert any(
                "confidence" in definition and ">=" in definition and "<=" in definition
                for definition in alias_checks
            )

            _assert_columns(
                _columns(cur, schema_name, "company_merge_log"),
                {
                    "id": ("uuid", None, "NO"),
                    "source_company_id": ("uuid", None, "NO"),
                    "target_company_id": ("uuid", None, "NO"),
                    "reason": ("text", None, "NO"),
                    "operator_id": ("uuid", None, "YES"),
                    "source_version": ("integer", None, "NO"),
                    "target_version": ("integer", None, "NO"),
                    "compensation_snapshot": ("jsonb", None, "NO"),
                    "created_at": ("timestamp with time zone", None, "NO"),
                },
            )
            merge_constraints = _constraints(cur, schema_name, "company_merge_log")
            _find_constraint(merge_constraints, "p", ("id",))
            _assert_fk(merge_constraints, "source_company_id", f"{schema_name}.companies", "a")
            _assert_fk(merge_constraints, "target_company_id", f"{schema_name}.companies", "a")
            _assert_fk(merge_constraints, "operator_id", f"{schema_name}.users", "n")
            merge_checks = _check_definitions(merge_constraints)
            assert sum("version > 0" in definition for definition in merge_checks) == 2
            assert any("source_company_id <> target_company_id" in definition for definition in merge_checks)

            _assert_columns(
                _columns(cur, schema_name, "outbox_events"),
                {
                    "event_id": ("uuid", None, "NO"),
                    "event_type": ("character varying", 64, "NO"),
                    "aggregate_type": ("character varying", 64, "NO"),
                    "aggregate_id": ("uuid", None, "NO"),
                    "schema_version": ("integer", None, "NO"),
                    "payload": ("jsonb", None, "NO"),
                    "occurred_at": ("timestamp with time zone", None, "NO"),
                    "published_at": ("timestamp with time zone", None, "YES"),
                    "attempt_count": ("integer", None, "NO"),
                    "last_error": ("text", None, "YES"),
                    "next_attempt_at": ("timestamp with time zone", None, "NO"),
                    "locked_by": ("character varying", 128, "YES"),
                    "locked_until": ("timestamp with time zone", None, "YES"),
                    "dead_lettered_at": ("timestamp with time zone", None, "YES"),
                },
            )
            outbox_constraints = _constraints(cur, schema_name, "outbox_events")
            _find_constraint(outbox_constraints, "p", ("event_id",))
            outbox_checks = _check_definitions(outbox_constraints)
            assert any("schema_version > 0" in definition for definition in outbox_checks)
            assert any("attempt_count >= 0" in definition for definition in outbox_checks)

            _assert_columns(
                _columns(cur, schema_name, "outbox_consumptions"),
                {
                    "event_id": ("uuid", None, "NO"),
                    "consumer_name": ("character varying", 128, "NO"),
                    "processed_at": ("timestamp with time zone", None, "NO"),
                },
            )
            consumption_constraints = _constraints(cur, schema_name, "outbox_consumptions")
            _find_constraint(consumption_constraints, "p", ("event_id", "consumer_name"))
            _assert_fk(consumption_constraints, "event_id", f"{schema_name}.outbox_events", "c")

            company_indexes = _indexes(cur, schema_name, "companies")
            alias_indexes = _indexes(cur, schema_name, "company_aliases")
            merge_indexes = _indexes(cur, schema_name, "company_merge_log")
            outbox_indexes = _indexes(cur, schema_name, "outbox_events")
            assert company_indexes["idx_companies_normalized_name"]["keys"] == ("normalized_name",)
            assert company_indexes["idx_companies_normalized_name_pattern"]["keys"] == (
                "normalized_name",
            )
            assert company_indexes["idx_companies_merged_into_id"]["keys"] == ("merged_into_id",)
            assert alias_indexes["idx_company_aliases_normalized_alias"]["keys"] == ("normalized_alias",)
            assert merge_indexes["idx_company_merge_log_source_company"]["keys"] == ("source_company_id",)
            assert merge_indexes["idx_company_merge_log_target_company"]["keys"] == ("target_company_id",)
            assert outbox_indexes["idx_outbox_pending"]["keys"] == (
                "next_attempt_at",
                "occurred_at",
            )
            assert outbox_indexes["idx_outbox_pending"]["predicate"] == (
                "((published_at IS NULL) AND (dead_lettered_at IS NULL))"
            )
