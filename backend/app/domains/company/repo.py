"""PostgreSQL read access for Company Identity."""

from typing import Any
from uuid import uuid4

from psycopg2.extras import Json

from app.db.postgres import PgCursor, get_cursor
from app.domains.company.normalization import normalize_company_name, normalize_credit_code


_MATCH_TYPE_ORDER = {
    "credit_code": 0,
    "legal_name": 1,
    "alias": 2,
    "prefix": 3,
}

_COMPANY_COLUMNS = """
    c.id,
    c.legal_name,
    c.normalized_name,
    c.unified_social_credit_code,
    c.registration_status,
    c.verification_status,
    c.identity_source,
    c.source_reference,
    c.identity_version,
    c.merged_into_id,
    c.created_at,
    c.updated_at,
    c.verified_at
"""

_RETURNING_COMPANY_COLUMNS = """
    id,
    legal_name,
    normalized_name,
    unified_social_credit_code,
    registration_status,
    verification_status,
    identity_source,
    source_reference,
    identity_version,
    merged_into_id,
    created_at,
    updated_at,
    verified_at
"""


def get_company_row(company_id: str) -> dict | None:
    """Return one stored company row without following logical merge redirects."""
    with get_cursor() as (_, cur):
        cur.execute(
            f"""
            SELECT {_COMPANY_COLUMNS}
            FROM companies AS c
            WHERE c.id = %s
            """,
            (company_id,),
        )
        return _row_to_dict(cur, cur.fetchone())


def find_by_credit_code_with_cursor(cur: PgCursor, credit_code: str) -> dict | None:
    """Return the company already owning a normalized credit code in this transaction."""
    cur.execute(
        f"""
        SELECT {_RETURNING_COMPANY_COLUMNS}
        FROM companies
        WHERE unified_social_credit_code = %s
        """,
        (credit_code,),
    )
    return _row_to_dict(cur, cur.fetchone())


def get_company_row_with_cursor(cur: PgCursor, company_id: str) -> dict | None:
    """Return a company row without following merge redirects in the caller transaction."""
    cur.execute(
        f"""
        SELECT {_RETURNING_COMPANY_COLUMNS}
        FROM companies
        WHERE id = %s
        """,
        (company_id,),
    )
    return _row_to_dict(cur, cur.fetchone())


def insert_company(
    cur: PgCursor,
    *,
    legal_name: str,
    normalized_name: str,
    unified_social_credit_code: str | None,
    registration_status: str | None,
    verification_status: str,
    identity_source: str,
    source_reference: str | None,
    created_by: str | None,
    verified_by: str | None = None,
) -> dict:
    """Insert a company using the caller-owned transaction."""
    company_id = str(uuid4())
    cur.execute(
        f"""
        INSERT INTO companies (
            id, legal_name, normalized_name, unified_social_credit_code,
            registration_status, verification_status, identity_source, source_reference,
            created_by, verified_by, verified_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  CASE WHEN %s = 'verified' THEN NOW() ELSE NULL END)
        RETURNING {_RETURNING_COMPANY_COLUMNS}
        """,
        (
            company_id,
            legal_name,
            normalized_name,
            unified_social_credit_code,
            registration_status,
            verification_status,
            identity_source,
            source_reference,
            created_by,
            verified_by,
            verification_status,
        ),
    )
    company = _row_to_dict(cur, cur.fetchone())
    assert company is not None
    return company


def insert_alias(
    cur: PgCursor,
    *,
    company_id: str,
    alias_name: str,
    normalized_alias: str,
    alias_type: str,
    source: str,
    confidence: float,
    created_by: str | None,
) -> str:
    """Insert one normalized alias using the caller-owned transaction."""
    alias_id = str(uuid4())
    cur.execute(
        """
        INSERT INTO company_aliases (
            id, company_id, alias_name, normalized_alias, alias_type, source, confidence, created_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            alias_id,
            company_id,
            alias_name,
            normalized_alias,
            alias_type,
            source,
            confidence,
            created_by,
        ),
    )
    return alias_id


def update_company(
    cur: PgCursor,
    company_id: str,
    expected_version: int,
    changes: dict[str, object],
) -> dict | None:
    """Apply an optimistic update to a current canonical company."""
    field_names = {
        "legal_name": "legal_name",
        "normalized_name": "normalized_name",
        "unified_social_credit_code": "unified_social_credit_code",
        "registration_status": "registration_status",
        "identity_source": "identity_source",
        "source_reference": "source_reference",
    }
    assignments: list[str] = []
    parameters: list[object] = []
    for field_name, column_name in field_names.items():
        if field_name in changes:
            assignments.append(f"{column_name} = %s")
            parameters.append(changes[field_name])
    assignments.extend(["identity_version = identity_version + 1", "updated_at = NOW()"])
    parameters.extend([company_id, expected_version])
    cur.execute(
        f"""
        UPDATE companies
        SET {", ".join(assignments)}
        WHERE id = %s
          AND identity_version = %s
          AND merged_into_id IS NULL
        RETURNING {_RETURNING_COMPANY_COLUMNS}
        """,
        parameters,
    )
    return _row_to_dict(cur, cur.fetchone())


def verify_company(
    cur: PgCursor,
    company_id: str,
    expected_version: int,
    unified_social_credit_code: str | None,
    identity_source: str,
    source_reference: str | None,
    verified_by: str | None,
) -> dict | None:
    """Verify a current canonical company using optimistic identity versioning."""
    cur.execute(
        f"""
        UPDATE companies
        SET unified_social_credit_code = %s,
            verification_status = 'verified',
            identity_source = %s,
            source_reference = %s,
            verified_by = %s,
            verified_at = NOW(),
            identity_version = identity_version + 1,
            updated_at = NOW()
        WHERE id = %s
          AND identity_version = %s
          AND merged_into_id IS NULL
        RETURNING {_RETURNING_COMPANY_COLUMNS}
        """,
        (
            unified_social_credit_code,
            identity_source,
            source_reference,
            verified_by,
            company_id,
            expected_version,
        ),
    )
    return _row_to_dict(cur, cur.fetchone())


def lock_companies_for_merge(cur: PgCursor, ids: list[str]) -> list[dict]:
    """Lock requested companies in UUID order to make opposite merge requests deadlock-safe."""
    sorted_ids = sorted(str(company_id) for company_id in ids)
    cur.execute(
        f"""
        SELECT {_RETURNING_COMPANY_COLUMNS}
        FROM companies
        WHERE id = ANY(%s::uuid[])
        ORDER BY id
        FOR UPDATE
        """,
        (sorted_ids,),
    )
    return _rows_to_dicts(cur)


def insert_company_merge_log(
    cur: PgCursor,
    *,
    source_company_id: str,
    target_company_id: str,
    reason: str,
    operator_id: str | None,
    source_version: int,
    target_version: int,
    compensation_snapshot: dict,
) -> str:
    """Persist the pre-merge state in the caller-owned transaction."""
    merge_log_id = str(uuid4())
    cur.execute(
        """
        INSERT INTO company_merge_log (
            id, source_company_id, target_company_id, reason, operator_id,
            source_version, target_version, compensation_snapshot
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            merge_log_id,
            source_company_id,
            target_company_id,
            reason,
            operator_id,
            source_version,
            target_version,
            Json(compensation_snapshot),
        ),
    )
    return merge_log_id


def apply_company_merge(
    cur: PgCursor,
    source_company_id: str,
    target_company_id: str,
) -> list[dict]:
    """Redirect the source and advance both identity versions in one statement."""
    cur.execute(
        f"""
        UPDATE companies
        SET merged_into_id = CASE
                WHEN id = %s THEN %s
                ELSE merged_into_id
            END,
            identity_version = identity_version + 1,
            updated_at = NOW()
        WHERE id = ANY(%s::uuid[])
        RETURNING {_RETURNING_COMPANY_COLUMNS}
        """,
        (
            source_company_id,
            target_company_id,
            [source_company_id, target_company_id],
        ),
    )
    return _rows_to_dicts(cur)


def search_identity_rows(query: str, limit: int) -> list[dict]:
    """Find deterministic identity matches before canonical merge resolution."""
    if limit < 1:
        raise ValueError("limit 必须大于 0")

    normalized_query = normalize_company_name(query)
    matches: list[dict] = []

    try:
        credit_code = normalize_credit_code(query)
    except ValueError:
        credit_code = None

    if credit_code is not None:
        matches.extend(
            _fetch_identity_rows(
                """
                SELECT {columns}, 'credit_code' AS match_type, 1.0::float AS confidence
                FROM companies AS c
                WHERE c.unified_social_credit_code = %s
                """,
                (credit_code,),
            )
        )

    matches.extend(
        _fetch_identity_rows(
            """
            SELECT {columns}, 'legal_name' AS match_type, 1.0::float AS confidence
            FROM companies AS c
            WHERE c.normalized_name = %s
            """,
            (normalized_query,),
        )
    )
    matches.extend(
        _fetch_identity_rows(
            """
            SELECT {columns}, 'alias' AS match_type, a.confidence::float AS confidence
            FROM companies AS c
            JOIN company_aliases AS a ON a.company_id = c.id
            WHERE a.normalized_alias = %s
            """,
            (normalized_query,),
        )
    )
    matches.extend(
        _fetch_identity_rows(
            """
            SELECT {columns}, 'prefix' AS match_type, 1.0::float AS confidence
            FROM companies AS c
            WHERE c.normalized_name LIKE %s ESCAPE '\\'
            """,
            (_escape_like(normalized_query) + "%",),
        )
    )

    deduplicated: dict[str, dict] = {}
    for row in sorted(matches, key=_identity_sort_key):
        deduplicated.setdefault(row["id"], row)
    return list(deduplicated.values())


def _fetch_identity_rows(query: str, parameters: tuple[Any, ...]) -> list[dict]:
    with get_cursor() as (_, cur):
        cur.execute(query.format(columns=_COMPANY_COLUMNS), parameters)
        return _rows_to_dicts(cur)


def _identity_sort_key(row: dict) -> tuple[int, int, float, str, str]:
    return (
        _MATCH_TYPE_ORDER[row["match_type"]],
        0 if row["verification_status"] == "verified" else 1,
        -float(row["confidence"]),
        row["legal_name"],
        row["id"],
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _rows_to_dicts(cur: PgCursor) -> list[dict]:
    columns = [description[0] for description in cur.description]
    return [_convert_row(dict(zip(columns, row))) for row in cur.fetchall()]


def _row_to_dict(cur: PgCursor, row: tuple[Any, ...] | None) -> dict | None:
    if row is None:
        return None
    columns = [description[0] for description in cur.description]
    return _convert_row(dict(zip(columns, row)))


def _convert_row(row: dict) -> dict:
    for field_name in ("id", "merged_into_id"):
        if row.get(field_name) is not None:
            row[field_name] = str(row[field_name])
    if row.get("confidence") is not None:
        row["confidence"] = float(row["confidence"])
    return row
