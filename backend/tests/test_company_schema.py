"""Integration coverage for the P1 company and outbox PostgreSQL schema."""

from collections.abc import Iterator
from contextlib import contextmanager

from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_conn, put_conn


P1_TABLES = {
    "companies",
    "company_aliases",
    "company_merge_log",
    "outbox_events",
    "outbox_consumptions",
}


@contextmanager
def _pg_cursor() -> Iterator[object]:
    """Yield a real configured PostgreSQL cursor without committing test work."""
    conn = get_conn()
    cur = None
    try:
        conn.rollback()
        cur = conn.cursor()
        yield cur
    finally:
        if cur is not None:
            cur.close()
        conn.rollback()
        put_conn(conn)


def _columns(cur, table_name: str) -> dict[str, tuple[str, str]]:
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table_name,),
    )
    return {name: (data_type, nullable) for name, data_type, nullable in cur.fetchall()}


def _constraints(cur, table_name: str) -> dict[str, tuple[str, str]]:
    cur.execute(
        """
        SELECT conname, contype, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = %s::regclass
        """,
        (f"public.{table_name}",),
    )
    return {name: (constraint_type, definition) for name, constraint_type, definition in cur.fetchall()}


def _indexes(cur, table_name: str) -> dict[str, str]:
    cur.execute(
        """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = %s
        """,
        (table_name,),
    )
    return dict(cur.fetchall())


def test_ensure_pg_schema_creates_required_p1_schema_idempotently():
    """Removing a P1 table, constraint, or index must make this fail."""
    ensure_pg_schema()
    ensure_pg_schema()

    with _pg_cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = ANY(%s)
            """,
            (list(P1_TABLES),),
        )
        assert {row[0] for row in cur.fetchall()} == P1_TABLES

        company_columns = _columns(cur, "companies")
        assert company_columns["id"] == ("uuid", "NO")
        assert company_columns["legal_name"] == ("character varying", "NO")
        assert company_columns["normalized_name"] == ("character varying", "NO")
        assert company_columns["unified_social_credit_code"] == ("character varying", "YES")
        assert company_columns["verification_status"] == ("character varying", "NO")
        assert company_columns["identity_version"] == ("integer", "NO")
        assert company_columns["merged_into_id"] == ("uuid", "YES")
        assert company_columns["verified_at"] == ("timestamp with time zone", "YES")

        company_constraints = _constraints(cur, "companies")
        assert company_constraints["companies_verification_status_check"][0] == "c"
        assert "verified" in company_constraints["companies_verification_status_check"][1]
        assert "pending_verification" in company_constraints["companies_verification_status_check"][1]
        assert company_constraints["companies_identity_version_check"][0] == "c"
        assert company_constraints["companies_merged_into_id_check"][0] == "c"
        assert company_constraints["companies_unified_social_credit_code_key"][0] == "u"
        assert company_constraints["companies_merged_into_id_fkey"][0] == "f"

        alias_columns = _columns(cur, "company_aliases")
        assert alias_columns["company_id"] == ("uuid", "NO")
        assert alias_columns["normalized_alias"] == ("character varying", "NO")
        assert alias_columns["confidence"] == ("numeric", "NO")
        alias_constraints = _constraints(cur, "company_aliases")
        assert alias_constraints["company_aliases_company_normalized_alias_key"][0] == "u"

        merge_columns = _columns(cur, "company_merge_log")
        assert merge_columns["source_company_id"] == ("uuid", "NO")
        assert merge_columns["target_company_id"] == ("uuid", "NO")
        assert merge_columns["source_version"] == ("integer", "NO")
        assert merge_columns["target_version"] == ("integer", "NO")
        assert merge_columns["compensation_snapshot"] == ("jsonb", "NO")

        outbox_columns = _columns(cur, "outbox_events")
        assert outbox_columns["event_id"] == ("uuid", "NO")
        assert outbox_columns["payload"] == ("jsonb", "NO")
        assert outbox_columns["attempt_count"] == ("integer", "NO")
        assert outbox_columns["next_attempt_at"] == ("timestamp with time zone", "NO")
        assert outbox_columns["locked_by"] == ("character varying", "YES")
        assert outbox_columns["locked_until"] == ("timestamp with time zone", "YES")
        assert outbox_columns["dead_lettered_at"] == ("timestamp with time zone", "YES")

        consumption_constraints = _constraints(cur, "outbox_consumptions")
        assert consumption_constraints["outbox_consumptions_pkey"][0] == "p"
        assert consumption_constraints["outbox_consumptions_event_id_fkey"][0] == "f"

        company_indexes = _indexes(cur, "companies")
        alias_indexes = _indexes(cur, "company_aliases")
        outbox_indexes = _indexes(cur, "outbox_events")
        assert "idx_companies_normalized_name" in company_indexes
        assert "idx_companies_merged_into_id" in company_indexes
        assert "idx_company_aliases_normalized_alias" in alias_indexes
        assert "idx_outbox_pending" in outbox_indexes
        assert "WHERE ((published_at IS NULL) AND (dead_lettered_at IS NULL))" in outbox_indexes[
            "idx_outbox_pending"
        ]
