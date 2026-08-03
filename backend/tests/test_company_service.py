"""Real PostgreSQL coverage for deterministic company identity resolution."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from psycopg2 import sql

from app.core.errors import DomainError
from app.db import init_pg
from app.db.postgres import get_conn, get_cursor, put_conn
from app.domains.company import repo as company_repo
from app.domains.company.service import get_company, search_identity


CREDIT_CODE_QUERY = "911100007109250324"
STABLE_CREDIT_CODE_QUERY = "91310000202608030B"

TEST_COMPANY_IDS = [
    f"00000000-0000-4000-8000-{number:012d}"
    for number in (
        501,
        511,
        521,
        531,
        541,
        542,
        551,
        561,
        571,
        572,
        581,
        582,
        583,
        584,
        *range(700, 722),
        731,
        732,
        801,
        802,
        803,
        804,
        805,
        821,
        822,
        823,
        824,
    )
]


def _insert_company(
    company_id: str,
    legal_name: str,
    *,
    credit_code: str | None = None,
    verification_status: str = "verified",
    merged_into_id: str | None = None,
    cursor_factory=get_cursor,
) -> None:
    with cursor_factory() as (_, cur):
        cur.execute(
            """
            INSERT INTO companies (
                id, legal_name, normalized_name, unified_social_credit_code,
                verification_status, identity_source, merged_into_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                company_id,
                legal_name,
                legal_name.casefold(),
                credit_code,
                verification_status,
                "manual",
                merged_into_id,
            ),
        )


def _insert_alias(
    alias_id: str,
    company_id: str,
    alias_name: str,
    confidence: float,
) -> None:
    with get_cursor() as (_, cur):
        cur.execute(
            """
            INSERT INTO company_aliases (
                id, company_id, alias_name, normalized_alias, alias_type, source, confidence
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                alias_id,
                company_id,
                alias_name,
                alias_name.casefold(),
                "short_name",
                "test",
                confidence,
            ),
        )


def _cleanup_test_companies() -> None:
    with get_cursor() as (_, cur):
        cur.execute(
            "DELETE FROM company_aliases WHERE company_id = ANY(%s::uuid[])",
            (TEST_COMPANY_IDS,),
        )
        cur.execute(
            "UPDATE companies SET merged_into_id = NULL WHERE id = ANY(%s::uuid[])",
            (TEST_COMPANY_IDS,),
        )
        cur.execute("DELETE FROM companies WHERE id = ANY(%s::uuid[])", (TEST_COMPANY_IDS,))


@pytest.fixture(autouse=True)
def _real_company_identity_database() -> Iterator[None]:
    """Keep every public-schema test scoped to its fixed company IDs."""
    init_pg.ensure_pg_schema()
    _cleanup_test_companies()
    try:
        yield
    finally:
        _cleanup_test_companies()
        with get_cursor() as (_, cur):
            cur.execute(
                "SELECT COUNT(*) FROM companies WHERE id = ANY(%s::uuid[])",
                (TEST_COMPANY_IDS,),
            )
            assert cur.fetchone() == (0,)


def test_search_identity_resolves_unique_credit_code_exactly() -> None:
    """Removing credit-code matching would turn this unique authoritative match into pending."""
    company_id = "00000000-0000-4000-8000-000000000501"
    _insert_company(
        company_id,
        "Task4 Credit Code Company",
        credit_code=CREDIT_CODE_QUERY,
        verification_status="pending_verification",
    )

    result = search_identity(f"  {CREDIT_CODE_QUERY}  ")

    assert result["resolution"] == "exact"
    assert result["exact"]["company_id"] == company_id
    assert result["exact"]["match_type"] == "credit_code"
    assert result["candidates"] == []


def test_search_identity_resolves_unique_verified_legal_name_exactly() -> None:
    """Downgrading verified legal-name matches would prevent the intended canonical resolution."""
    company_id = "00000000-0000-4000-8000-000000000511"
    legal_name = "Task4 Verified Legal Name Company"
    _insert_company(company_id, legal_name)

    result = search_identity(legal_name)

    assert result["resolution"] == "exact"
    assert result["exact"]["company_id"] == company_id
    assert result["exact"]["match_type"] == "legal_name"
    assert result["candidates"] == []


def test_search_identity_resolves_high_confidence_unique_verified_alias_exactly() -> None:
    """Raising the alias threshold above .95 would reject this documented exact alias match."""
    company_id = "00000000-0000-4000-8000-000000000521"
    alias_name = "Task4 Exact Alias"
    _insert_company(company_id, "Task4 Alias Exact Company")
    _insert_alias("10000000-0000-4000-8000-000000000521", company_id, alias_name, 0.95)

    result = search_identity(alias_name)

    assert result["resolution"] == "exact"
    assert result["exact"]["company_id"] == company_id
    assert result["exact"]["match_type"] == "alias"
    assert result["exact"]["confidence"] == 0.95


def test_search_identity_returns_candidates_for_low_confidence_alias() -> None:
    """Auto-resolving a .94 alias would bind a company without sufficient confidence."""
    company_id = "00000000-0000-4000-8000-000000000531"
    alias_name = "Task4 Low Confidence Alias"
    _insert_company(company_id, "Task4 Alias Candidate Company")
    _insert_alias("10000000-0000-4000-8000-000000000531", company_id, alias_name, 0.94)

    result = search_identity(alias_name)

    assert result["resolution"] == "candidates"
    assert result["exact"] is None
    assert [candidate["company_id"] for candidate in result["candidates"]] == [company_id]


def test_search_identity_returns_candidates_for_ambiguous_alias() -> None:
    """Resolving an alias owned by two companies would hide a real identity ambiguity."""
    alias_name = "Task4 Ambiguous Alias"
    alpha_id = "00000000-0000-4000-8000-000000000541"
    beta_id = "00000000-0000-4000-8000-000000000542"
    _insert_company(alpha_id, "Task4 Alias Ambiguous Alpha")
    _insert_company(beta_id, "Task4 Alias Ambiguous Beta")
    _insert_alias("10000000-0000-4000-8000-000000000541", alpha_id, alias_name, 0.99)
    _insert_alias("10000000-0000-4000-8000-000000000542", beta_id, alias_name, 0.99)

    result = search_identity(alias_name)

    assert result["resolution"] == "candidates"
    assert result["exact"] is None
    assert [candidate["company_id"] for candidate in result["candidates"]] == [alpha_id, beta_id]


def test_search_identity_never_auto_resolves_a_unique_prefix_match() -> None:
    """Treating a prefix as exact would conflate incomplete user input with a legal identity."""
    company_id = "00000000-0000-4000-8000-000000000551"
    _insert_company(company_id, "Task4 Prefix Sole Company")

    result = search_identity("Task4 Prefix Sole")

    assert result["resolution"] == "candidates"
    assert result["exact"] is None
    assert result["candidates"][0]["company_id"] == company_id
    assert result["candidates"][0]["match_type"] == "prefix"


def test_search_identity_marks_unique_pending_legal_name_for_verification() -> None:
    """Resolving an unverified legal name exactly would bypass the verification workflow."""
    company_id = "00000000-0000-4000-8000-000000000561"
    legal_name = "Task4 Pending Legal Name Company"
    _insert_company(company_id, legal_name, verification_status="pending_verification")

    result = search_identity(legal_name)

    assert result == {"resolution": "pending_verification", "exact": None, "candidates": []}


def test_get_company_and_search_identity_redirect_to_the_canonical_company() -> None:
    """Returning the merged row would expose a non-canonical enterprise to callers."""
    source_id = "00000000-0000-4000-8000-000000000571"
    canonical_id = "00000000-0000-4000-8000-000000000572"
    source_name = "Task4 Redirect Source Company"
    _insert_company(canonical_id, "Task4 Redirect Canonical Company")
    _insert_company(source_id, source_name, merged_into_id=canonical_id)

    company = get_company(source_id)
    resolution = search_identity(source_name)

    assert company["id"] == canonical_id
    assert company["redirected_from"] == source_id
    assert resolution["resolution"] == "exact"
    assert resolution["exact"]["company_id"] == canonical_id
    assert resolution["exact"]["redirected_from"] == source_id


def test_search_identity_prioritizes_unique_credit_code_over_lower_match_classes() -> None:
    """Returning candidates here would let legal, alias, or prefix matches override a unique credit code."""
    credit_id = "00000000-0000-4000-8000-000000000581"
    legal_id = "00000000-0000-4000-8000-000000000582"
    alias_id = "00000000-0000-4000-8000-000000000583"
    prefix_id = "00000000-0000-4000-8000-000000000584"
    _insert_company(credit_id, STABLE_CREDIT_CODE_QUERY, credit_code=STABLE_CREDIT_CODE_QUERY)
    _insert_company(legal_id, STABLE_CREDIT_CODE_QUERY)
    _insert_company(alias_id, "Task4 Stable Alias Company")
    _insert_company(prefix_id, f"{STABLE_CREDIT_CODE_QUERY} Prefix Company")
    _insert_alias(
        "10000000-0000-4000-8000-000000000583",
        alias_id,
        STABLE_CREDIT_CODE_QUERY,
        1.0,
    )

    result = search_identity(STABLE_CREDIT_CODE_QUERY)

    assert result["resolution"] == "exact"
    assert result["exact"]["company_id"] == credit_id
    assert result["exact"]["match_type"] == "credit_code"
    assert result["candidates"] == []


def test_search_identity_resolves_all_merged_rows_before_applying_limit() -> None:
    """Limiting source rows first would hide the independent legal-name candidate behind redirects."""
    query = "Task4 Round1 Shared Legal Name"
    canonical_a_id = "00000000-0000-4000-8000-000000000801"
    source_ids = [
        "00000000-0000-4000-8000-000000000802",
        "00000000-0000-4000-8000-000000000803",
        "00000000-0000-4000-8000-000000000804",
    ]
    canonical_b_id = "00000000-0000-4000-8000-000000000805"
    _insert_company(canonical_a_id, "Task4 Round1 Canonical A")
    for source_id in source_ids:
        _insert_company(source_id, query, merged_into_id=canonical_a_id)
    _insert_company(canonical_b_id, query)

    result = search_identity(query, limit=2)

    assert result["resolution"] == "candidates"
    assert result["exact"] is None
    assert [candidate["company_id"] for candidate in result["candidates"]] == [
        canonical_a_id,
        canonical_b_id,
    ]


def test_search_identity_sorts_canonical_candidates_before_applying_limit() -> None:
    """Sorting source names or limiting them first would return Canonical Zulu ahead of Canonical Alpha."""
    query = "Task4 Round1 Redirect Alias"
    zulu_source_id = "00000000-0000-4000-8000-000000000821"
    zulu_canonical_id = "00000000-0000-4000-8000-000000000822"
    alpha_source_id = "00000000-0000-4000-8000-000000000823"
    alpha_canonical_id = "00000000-0000-4000-8000-000000000824"
    _insert_company(zulu_canonical_id, "Task4 Round1 Canonical Zulu")
    _insert_company(alpha_canonical_id, "Task4 Round1 Canonical Alpha")
    _insert_company(
        zulu_source_id,
        "Task4 Round1 Source Alpha",
        merged_into_id=zulu_canonical_id,
    )
    _insert_company(
        alpha_source_id,
        "Task4 Round1 Source Zulu",
        merged_into_id=alpha_canonical_id,
    )
    _insert_alias("20000000-0000-4000-8000-000000000821", zulu_source_id, query, 0.99)
    _insert_alias("20000000-0000-4000-8000-000000000823", alpha_source_id, query, 0.99)

    result = search_identity(query, limit=1)

    assert result["resolution"] == "candidates"
    assert result["exact"] is None
    assert [candidate["company_id"] for candidate in result["candidates"]] == [
        alpha_canonical_id,
    ]
    assert result["candidates"][0]["match_type"] == "alias"


def test_get_company_returns_none_when_the_requested_company_is_absent() -> None:
    """Treating an absent requested ID as merge corruption would break ordinary not-found reads."""
    assert get_company("00000000-0000-4000-8000-000000000691") is None


def test_get_company_allows_twenty_redirect_hops_and_rejects_the_twenty_first() -> None:
    """Removing the cap permits unbounded merge traversal; lowering it rejects valid 20-hop data."""
    chain_ids = [f"00000000-0000-4000-8000-{number:012d}" for number in range(700, 722)]
    for index in range(len(chain_ids) - 1, -1, -1):
        company_id = chain_ids[index]
        _insert_company(
            company_id,
            f"Task4 Redirect Hop {index:02d}",
            merged_into_id=chain_ids[index + 1] if index + 1 < len(chain_ids) else None,
        )

    within_cap = get_company(chain_ids[1])
    with pytest.raises(DomainError) as exc_info:
        get_company(chain_ids[0])

    assert within_cap["id"] == chain_ids[-1]
    assert within_cap["redirected_from"] == chain_ids[1]
    assert exc_info.value.code == "COMPANY_MERGE_INTEGRITY_ERROR"


def test_get_company_rejects_a_real_postgres_redirect_cycle() -> None:
    """Without repeated-ID detection, a two-company merge cycle never reaches a canonical row."""
    first_id = "00000000-0000-4000-8000-000000000731"
    second_id = "00000000-0000-4000-8000-000000000732"
    _insert_company(first_id, "Task4 Cycle First Company")
    _insert_company(second_id, "Task4 Cycle Second Company", merged_into_id=first_id)
    with get_cursor() as (_, cur):
        cur.execute("UPDATE companies SET merged_into_id = %s WHERE id = %s", (second_id, first_id))

    with pytest.raises(DomainError) as exc_info:
        get_company(first_id)

    assert exc_info.value.code == "COMPANY_MERGE_INTEGRITY_ERROR"


@contextmanager
def _isolated_company_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Build a real isolated schema so only this test can model an impossible broken FK."""
    schema_name = f"task4_company_{uuid4().hex}"
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
                    sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema_name))
                )
                yield conn, cur
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                cur.close()

        monkeypatch.setattr(init_pg, "get_cursor", schema_cursor)
        init_pg.ensure_pg_schema()
        monkeypatch.setattr(company_repo, "get_cursor", schema_cursor)
        yield schema_cursor
    finally:
        conn.rollback()
        if created:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO public")
                cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name)))
            conn.commit()
        put_conn(conn)


def test_get_company_rejects_a_broken_redirect_chain_in_isolated_postgres_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Following a missing merge target must fail closed instead of guessing a canonical company."""
    source_id = "00000000-0000-4000-8000-000000000741"
    missing_target_id = "00000000-0000-4000-8000-000000000742"
    with _isolated_company_schema(monkeypatch) as schema_cursor:
        with schema_cursor() as (_, cur):
            cur.execute("ALTER TABLE companies DROP CONSTRAINT companies_merged_into_id_fkey")
        _insert_company(
            source_id,
            "Task4 Broken Chain Source Company",
            merged_into_id=missing_target_id,
            cursor_factory=schema_cursor,
        )

        with pytest.raises(DomainError) as exc_info:
            get_company(source_id)

    assert exc_info.value.code == "COMPANY_MERGE_INTEGRITY_ERROR"
