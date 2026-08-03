"""PostgreSQL read access for Company Identity."""

from typing import Any

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
