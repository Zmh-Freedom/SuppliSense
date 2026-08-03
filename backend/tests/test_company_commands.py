"""Real PostgreSQL coverage for Company Identity write commands."""

from collections.abc import Iterator
from uuid import uuid4

import pytest

from app.core.errors import DomainError
from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_cursor
from app.domains.company.service import create_company, update_company, verify_company
from app.schemas.company import CompanyAliasInput, CompanyCreateInput, CompanyUpdateInput, CompanyVerifyInput


_CREDIT_CODE_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_CREDIT_CODE_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)


def _unique_valid_credit_code() -> str:
    """Generate a valid, per-test credit code without relying on production normalization."""
    body = "91" + "".join(char for char in uuid4().hex.upper() if char in _CREDIT_CODE_CHARS)[:15]
    total = sum(
        _CREDIT_CODE_CHARS.index(char) * weight
        for char, weight in zip(body, _CREDIT_CODE_WEIGHTS)
    )
    return body + _CREDIT_CODE_CHARS[(31 - total % 31) % 31]


@pytest.fixture(autouse=True)
def _company_command_database() -> Iterator[list[str]]:
    """Scope cleanup to the UUIDs created by this test, never broad table deletion."""
    ensure_pg_schema()
    company_ids: list[str] = []
    try:
        yield company_ids
    finally:
        if company_ids:
            with get_cursor() as (_, cur):
                cur.execute(
                    "DELETE FROM outbox_consumptions WHERE event_id IN ("
                    "SELECT event_id FROM outbox_events WHERE aggregate_id = ANY(%s::uuid[]))",
                    (company_ids,),
                )
                cur.execute(
                    "DELETE FROM outbox_events WHERE aggregate_id = ANY(%s::uuid[])",
                    (company_ids,),
                )
                cur.execute("DELETE FROM audit_logs WHERE resource_id = ANY(%s)", (company_ids,))
                cur.execute("DELETE FROM company_aliases WHERE company_id = ANY(%s::uuid[])", (company_ids,))
                cur.execute("UPDATE companies SET merged_into_id = NULL WHERE merged_into_id = ANY(%s::uuid[])", (company_ids,))
                cur.execute("DELETE FROM companies WHERE id = ANY(%s::uuid[])", (company_ids,))


def _create_pending(company_ids: list[str], legal_name: str) -> dict:
    result = create_company(CompanyCreateInput(legal_name=legal_name), None, "analyst")
    company_ids.append(result["company_id"])
    return result


def _insert_merged_subject(company_ids: list[str]) -> str:
    target_id = str(uuid4())
    merged_id = str(uuid4())
    company_ids.extend([target_id, merged_id])
    with get_cursor() as (_, cur):
        cur.execute(
            """
            INSERT INTO companies (
                id, legal_name, normalized_name, verification_status, identity_source
            ) VALUES (%s, %s, %s, %s, %s), (%s, %s, %s, %s, %s)
            """,
            (
                target_id,
                "Task5 Canonical Company",
                "task5 canonical company",
                "pending_verification",
                "manual",
                merged_id,
                "Task5 Merged Company",
                "task5 merged company",
                "pending_verification",
                "manual",
            ),
        )
        cur.execute("UPDATE companies SET merged_into_id = %s WHERE id = %s", (target_id, merged_id))
    return merged_id


def test_analyst_cannot_create_a_verified_company() -> None:
    """Removing the create-role guard would let analysts bypass verification review."""
    data = CompanyCreateInput(
        legal_name="Task5 Analyst Verified Company",
        verification_status="verified",
        identity_source="admin_verified",
        source_reference="manual-review-20260803",
    )

    with pytest.raises(DomainError) as exc_info:
        create_company(data, None, "analyst")

    assert exc_info.value.code == "COMPANY_VERIFICATION_FORBIDDEN"


def test_admin_verified_create_requires_valid_credit_code_or_trusted_reference() -> None:
    """Accepting an unverified manual assertion would create an authoritative identity without evidence."""
    data = CompanyCreateInput(
        legal_name="Task5 Unsupported Verified Company",
        verification_status="verified",
        identity_source="manual",
        source_reference="untrusted-claim",
    )

    with pytest.raises(DomainError) as exc_info:
        create_company(data, None, "admin")

    assert exc_info.value.code == "COMPANY_VERIFICATION_EVIDENCE_REQUIRED"


def test_create_persists_normalized_aliases_audit_and_outbox_event(
    _company_command_database: list[str],
) -> None:
    """Dropping any write or alias normalization would leave the committed identity projection incomplete."""
    result = create_company(
        CompanyCreateInput(
            legal_name="Task5 Alias Company",
            aliases=[
                CompanyAliasInput(alias_name="  Ｔａｓｋ５   别名  ", alias_type="short_name"),
                CompanyAliasInput(alias_name="Task5 Former Name", alias_type="former_name"),
            ],
        ),
        None,
        "analyst",
    )
    company_id = result["company_id"]
    _company_command_database.append(company_id)

    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT normalized_name, verification_status, identity_version FROM companies WHERE id = %s",
            (company_id,),
        )
        assert cur.fetchone() == ("task5 alias company", "pending_verification", 1)
        cur.execute(
            "SELECT alias_name, normalized_alias FROM company_aliases WHERE company_id = %s ORDER BY alias_name",
            (company_id,),
        )
        assert set(cur.fetchall()) == {
            ("Ｔａｓｋ５   别名", "task5 别名"),
            ("Task5 Former Name", "task5 former name"),
        }
        cur.execute(
            "SELECT action, resource_type FROM audit_logs WHERE resource_id = %s",
            (company_id,),
        )
        assert cur.fetchall() == [("company.created", "company")]
        cur.execute(
            "SELECT event_type, payload FROM outbox_events WHERE aggregate_id = %s",
            (company_id,),
        )
        assert cur.fetchone() == (
            "company.created",
            {
                "company_id": company_id,
                "legal_name": "Task5 Alias Company",
                "identity_version": 1,
                "verification_status": "pending_verification",
            },
        )


def test_create_duplicate_credit_code_reports_the_conflicting_company(
    _company_command_database: list[str],
) -> None:
    """Replacing a conflict with a generic database error would hide the existing canonical company."""
    credit_code = _unique_valid_credit_code()
    existing = create_company(
        CompanyCreateInput(
            legal_name="Task5 Existing Credit Code Company",
            unified_social_credit_code=credit_code,
            verification_status="verified",
            identity_source="admin_verified",
        ),
        None,
        "admin",
    )
    _company_command_database.append(existing["company_id"])

    with pytest.raises(DomainError) as exc_info:
        create_company(
            CompanyCreateInput(
                legal_name="Task5 Duplicate Credit Code Company",
                unified_social_credit_code=credit_code,
            ),
            None,
            "analyst",
        )

    assert exc_info.value.code == "COMPANY_ALREADY_EXISTS"
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {"company_id": existing["company_id"]}


def test_create_rejects_duplicate_normalized_aliases_before_writing_company(
    _company_command_database: list[str],
) -> None:
    """Allowing duplicate aliases to reach the database would leak a storage error as a credit-code conflict."""
    legal_name = f"Task5 Duplicate Alias {uuid4().hex}"

    with pytest.raises(DomainError) as exc_info:
        create_company(
            CompanyCreateInput(
                legal_name=legal_name,
                aliases=[
                    CompanyAliasInput(alias_name="  Ｔａｓｋ５ Alias ", alias_type="short_name"),
                    CompanyAliasInput(alias_name="task5   alias", alias_type="former_name"),
                ],
            ),
            None,
            "analyst",
        )

    assert exc_info.value.code == "COMPANY_DUPLICATE_ALIAS"
    assert exc_info.value.status_code == 422
    with get_cursor() as (_, cur):
        cur.execute("SELECT COUNT(*) FROM companies WHERE legal_name = %s", (legal_name,))
        assert cur.fetchone() == (0,)


def test_analyst_can_update_operational_fields_but_not_authoritative_identity_fields(
    _company_command_database: list[str],
) -> None:
    """Allowing analysts to replace identity evidence would bypass the administrator authority boundary."""
    company = _create_pending(_company_command_database, "Task5 Analyst Update Company")

    updated = update_company(
        company["company_id"],
        CompanyUpdateInput(expected_version=1, registration_status="active"),
        None,
        "analyst",
    )
    with pytest.raises(DomainError) as exc_info:
        update_company(
            company["company_id"],
            CompanyUpdateInput(expected_version=2, identity_source="import"),
            None,
            "analyst",
        )

    assert updated["identity_version"] == 2
    assert exc_info.value.code == "COMPANY_IDENTITY_FIELD_FORBIDDEN"


def test_analyst_cannot_rename_a_company(
    _company_command_database: list[str],
) -> None:
    """Treating legal names as operational data would let analysts alter a legal identity."""
    company = _create_pending(_company_command_database, "Task5 Analyst Rename Company")

    with pytest.raises(DomainError) as exc_info:
        update_company(
            company["company_id"],
            CompanyUpdateInput(expected_version=1, legal_name="Task5 Renamed Legal Entity"),
            None,
            "analyst",
        )

    assert exc_info.value.code == "COMPANY_IDENTITY_FIELD_FORBIDDEN"


@pytest.mark.parametrize("field_name", ["legal_name", "identity_source"])
def test_update_rejects_explicit_none_for_non_null_fields(
    _company_command_database: list[str],
    field_name: str,
) -> None:
    """Passing NULL through to a non-null column would leak a PostgreSQL error instead of a domain validation error."""
    company = _create_pending(_company_command_database, f"Task5 Null {field_name} Company")

    with pytest.raises(DomainError) as exc_info:
        update_company(
            company["company_id"],
            CompanyUpdateInput(expected_version=1, **{field_name: None}),
            None,
            "admin",
        )

    assert exc_info.value.code == "COMPANY_INVALID_UPDATE"
    assert exc_info.value.status_code == 422


def test_update_rejects_expected_version_only_patch(
    _company_command_database: list[str],
) -> None:
    """Incrementing the identity version for no data change would manufacture a false concurrent update."""
    company = _create_pending(_company_command_database, "Task5 Empty Update Company")

    with pytest.raises(DomainError) as exc_info:
        update_company(
            company["company_id"],
            CompanyUpdateInput(expected_version=1),
            None,
            "admin",
        )

    assert exc_info.value.code == "COMPANY_UPDATE_REQUIRED"
    assert exc_info.value.status_code == 422


def test_update_reports_not_found_merged_subject_and_stale_version(
    _company_command_database: list[str],
) -> None:
    """Ignoring either predicate would permit lost updates or edits to a redirected subject."""
    company = _create_pending(_company_command_database, "Task5 Versioned Company")
    merged_company_id = _insert_merged_subject(_company_command_database)

    with pytest.raises(DomainError) as missing_error:
        update_company(
            str(uuid4()),
            CompanyUpdateInput(expected_version=1, registration_status="active"),
            None,
            "admin",
        )

    with pytest.raises(DomainError) as stale_error:
        update_company(
            company["company_id"],
            CompanyUpdateInput(expected_version=2, registration_status="active"),
            None,
            "admin",
        )
    with pytest.raises(DomainError) as merged_error:
        update_company(
            merged_company_id,
            CompanyUpdateInput(expected_version=1, registration_status="active"),
            None,
            "admin",
        )

    assert missing_error.value.code == "COMPANY_NOT_FOUND"
    assert merged_error.value.code == "COMPANY_MERGED_SUBJECT"
    assert stale_error.value.code == "COMPANY_VERSION_CONFLICT"


def test_verify_requires_admin_and_trusted_evidence_then_writes_event(
    _company_command_database: list[str],
) -> None:
    """Skipping evidence, role, version, or side-effect writes would leave verification unaudited or unauthorized."""
    company = _create_pending(_company_command_database, "Task5 Verify Company")
    request = CompanyVerifyInput(
        expected_version=1,
        identity_source="admin_verified",
        source_reference="case-20260803-001",
    )

    with pytest.raises(DomainError) as forbidden_error:
        verify_company(company["company_id"], request, None, "analyst")
    verified = verify_company(company["company_id"], request, None, "admin")

    assert forbidden_error.value.code == "COMPANY_VERIFICATION_FORBIDDEN"
    assert verified["verification_status"] == "verified"
    assert verified["identity_version"] == 2
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT verification_status, identity_source, source_reference, verified_at IS NOT NULL "
            "FROM companies WHERE id = %s",
            (company["company_id"],),
        )
        assert cur.fetchone() == ("verified", "admin_verified", "case-20260803-001", True)
        cur.execute(
            "SELECT action FROM audit_logs WHERE resource_id = %s ORDER BY action",
            (company["company_id"],),
        )
        assert cur.fetchall() == [("company.created",), ("company.verified",)]
        cur.execute(
            "SELECT event_type, payload->>'identity_version' FROM outbox_events "
            "WHERE aggregate_id = %s ORDER BY event_type",
            (company["company_id"],),
        )
        assert cur.fetchall() == [("company.created", "1"), ("company.verified", "2")]
