"""Real PostgreSQL coverage for auditable, deadlock-safe company merges."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

import pytest
from psycopg2 import sql
from psycopg2.errors import CheckViolation, DeadlockDetected
from pydantic import ValidationError

from app.core.errors import DomainError
from app.db import init_pg
from app.db.postgres import get_conn, put_conn
from app.domains.company import repo as company_repo
from app.domains.company import service as company_service
from app.domains.company.service import get_company, merge_company, search_identity
from app.schemas.company import CompanyAliasInput, CompanyCreateInput, CompanyMergeInput


SOURCE_ID = "00000000-0000-0000-0000-000000000001"
TARGET_ID = "00000000-0000-0000-0000-000000000002"


@contextmanager
def _isolated_company_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Run every merge case in a disposable real PostgreSQL schema."""
    schema_name = f"task6_company_merge_{uuid4().hex}"
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
                cur.execute(
                    sql.SQL("SET LOCAL search_path TO {}, public").format(
                        sql.Identifier(schema_name)
                    )
                )
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
        monkeypatch.setattr(company_repo, "get_cursor", schema_cursor)
        monkeypatch.setattr(company_service, "get_cursor", schema_cursor)
        yield schema_cursor
    finally:
        setup_conn.rollback()
        if created:
            with setup_conn.cursor() as cur:
                cur.execute("SET search_path TO public")
                cur.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema_name)
                    )
                )
            setup_conn.commit()
        put_conn(setup_conn)


def _merge_input(
    target_company_id: str,
    *,
    source_expected_version: int = 1,
    target_expected_version: int = 1,
    confirm: bool = True,
    reason: str = "重复档案",
) -> CompanyMergeInput:
    return CompanyMergeInput(
        target_company_id=target_company_id,
        reason=reason,
        confirm=confirm,
        source_expected_version=source_expected_version,
        target_expected_version=target_expected_version,
    )


def _create_company(
    legal_name: str,
    *,
    aliases: list[CompanyAliasInput] | None = None,
    credit_code: str | None = None,
) -> dict:
    return company_service.create_company(
        CompanyCreateInput(
            legal_name=legal_name,
            aliases=aliases or [],
            unified_social_credit_code=credit_code,
            verification_status="verified",
            identity_source="admin_verified",
            source_reference=f"task6-{uuid4().hex}",
        ),
        None,
        "admin",
    )


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_merge_requires_admin(role: str) -> None:
    """Dropping the role guard would let non-administrators permanently redirect an identity."""
    with pytest.raises(DomainError) as exc_info:
        merge_company(SOURCE_ID, _merge_input(TARGET_ID), "u-1", role)

    assert exc_info.value.status_code == 403


def test_merge_requires_explicit_confirmation() -> None:
    """Merging when confirm is false would make a destructive identity action accidental."""
    with pytest.raises(DomainError) as exc_info:
        merge_company(SOURCE_ID, _merge_input(TARGET_ID, confirm=False), "u-1", "admin")

    assert exc_info.value.code == "COMPANY_MERGE_CONFIRMATION_REQUIRED"


def test_merge_rejects_invalid_source_uuid_before_opening_a_transaction() -> None:
    """Passing an arbitrary source ID to PostgreSQL would leak a driver validation error."""
    with pytest.raises(DomainError) as exc_info:
        merge_company("not-a-uuid", _merge_input(TARGET_ID), None, "admin")

    assert exc_info.value.code == "COMPANY_INVALID_ID"
    assert exc_info.value.status_code == 422


def test_merge_schema_rejects_invalid_target_uuid() -> None:
    """Accepting a malformed target would defer request validation to the service or database."""
    with pytest.raises(ValidationError):
        _merge_input("not-a-uuid")


def test_merge_rejects_conflicting_credit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirecting two distinct credited legal entities would destroy a deterministic identity boundary."""
    with _isolated_company_schema(monkeypatch):
        source = _create_company("Task6 Conflicting Source", credit_code="911100007109250324")
        target = _create_company("Task6 Conflicting Target", credit_code="91440300708461136T")

        with pytest.raises(DomainError) as exc_info:
            merge_company(source["company_id"], _merge_input(target["company_id"]), None, "admin")

    assert exc_info.value.code == "COMPANY_MERGE_IDENTITY_CONFLICT"


@pytest.mark.parametrize("missing_source", [True, False])
def test_merge_reports_missing_source_or_target(
    monkeypatch: pytest.MonkeyPatch,
    missing_source: bool,
) -> None:
    """Collapsing absent rows into a version conflict would hide an invalid merge request."""
    with _isolated_company_schema(monkeypatch):
        existing = _create_company("Task6 Existing Company")
        source_id = str(uuid4()) if missing_source else existing["company_id"]
        target_id = existing["company_id"] if missing_source else str(uuid4())

        with pytest.raises(DomainError) as exc_info:
            merge_company(source_id, _merge_input(target_id), None, "admin")

    assert exc_info.value.code == "COMPANY_NOT_FOUND"


def test_merge_rejects_self_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allowing a source to become its own target would create an unusable redirect cycle."""
    with _isolated_company_schema(monkeypatch):
        company = _create_company("Task6 Self Merge Company")

        with pytest.raises(DomainError) as exc_info:
            merge_company(
                company["company_id"],
                _merge_input(company["company_id"]),
                None,
                "admin",
            )

    assert exc_info.value.code == "COMPANY_MERGE_SELF_REFERENCE"


def test_merge_rejects_already_merged_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    """Permitting a redirected row into another merge would make canonical identity depend on merge order."""
    with _isolated_company_schema(monkeypatch):
        source = _create_company("Task6 Already Merged Source")
        target = _create_company("Task6 Already Merged Target")
        final_target = _create_company("Task6 Final Target")
        merge_company(source["company_id"], _merge_input(target["company_id"]), None, "admin")

        with pytest.raises(DomainError) as exc_info:
            merge_company(
                source["company_id"],
                _merge_input(final_target["company_id"]),
                None,
                "admin",
            )

    assert exc_info.value.code == "COMPANY_MERGED_SUBJECT"


def test_merge_rejects_an_already_merged_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treating a redirected target as canonical would make the requested target ambiguous."""
    with _isolated_company_schema(monkeypatch):
        source = _create_company("Task6 Merged Target Source")
        target = _create_company("Task6 Merged Target")
        canonical = _create_company("Task6 Merged Target Canonical")
        merge_company(target["company_id"], _merge_input(canonical["company_id"]), None, "admin")

        with pytest.raises(DomainError) as exc_info:
            merge_company(source["company_id"], _merge_input(target["company_id"]), None, "admin")

    assert exc_info.value.code == "COMPANY_MERGED_SUBJECT"


@pytest.mark.parametrize(
    ("source_expected_version", "target_expected_version"),
    [(2, 1), (1, 2)],
)
def test_merge_rejects_stale_source_or_target_version(
    monkeypatch: pytest.MonkeyPatch,
    source_expected_version: int,
    target_expected_version: int,
) -> None:
    """Ignoring either expected version would let a merge overwrite a concurrent identity change."""
    with _isolated_company_schema(monkeypatch):
        source = _create_company("Task6 Versioned Source")
        target = _create_company("Task6 Versioned Target")

        with pytest.raises(DomainError) as exc_info:
            merge_company(
                source["company_id"],
                _merge_input(
                    target["company_id"],
                    source_expected_version=source_expected_version,
                    target_expected_version=target_expected_version,
                ),
                None,
                "admin",
            )

    assert exc_info.value.code == "COMPANY_VERSION_CONFLICT"


def test_merge_rejects_target_redirect_chain_that_reaches_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creating source-to-target over an existing target-to-source link would persist a redirect cycle."""
    with _isolated_company_schema(monkeypatch) as schema_cursor:
        source = _create_company("Task6 Cycle Source")
        target = _create_company("Task6 Cycle Target")
        with schema_cursor() as (_, cur):
            cur.execute(
                "UPDATE companies SET merged_into_id = %s WHERE id = %s",
                (source["company_id"], target["company_id"]),
            )

        with pytest.raises(DomainError) as exc_info:
            merge_company(source["company_id"], _merge_input(target["company_id"]), None, "admin")

    assert exc_info.value.code == "COMPANY_MERGE_INTEGRITY_ERROR"


def test_merge_rejects_target_redirect_cycle_not_involving_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing target-only cycle must fail closed even when it never reaches the source."""
    with _isolated_company_schema(monkeypatch) as schema_cursor:
        source = _create_company("Task6 Independent Cycle Source")
        target = _create_company("Task6 Independent Cycle Target")
        cycle_member = _create_company("Task6 Independent Cycle Member")
        with schema_cursor() as (_, cur):
            cur.execute(
                "UPDATE companies SET merged_into_id = %s WHERE id = %s",
                (cycle_member["company_id"], target["company_id"]),
            )
            cur.execute(
                "UPDATE companies SET merged_into_id = %s WHERE id = %s",
                (target["company_id"], cycle_member["company_id"]),
            )

        with pytest.raises(DomainError) as exc_info:
            merge_company(source["company_id"], _merge_input(target["company_id"]), None, "admin")

    assert exc_info.value.code == "COMPANY_MERGE_INTEGRITY_ERROR"


def test_merge_rejects_broken_target_redirect_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """A target that points at a missing row cannot safely become a canonical merge target."""
    with _isolated_company_schema(monkeypatch) as schema_cursor:
        source = _create_company("Task6 Broken Target Source")
        target = _create_company("Task6 Broken Target")
        with schema_cursor() as (_, cur):
            cur.execute("ALTER TABLE companies DROP CONSTRAINT companies_merged_into_id_fkey")
            cur.execute(
                "UPDATE companies SET merged_into_id = %s WHERE id = %s",
                (str(uuid4()), target["company_id"]),
            )

        with pytest.raises(DomainError) as exc_info:
            merge_company(source["company_id"], _merge_input(target["company_id"]), None, "admin")

    assert exc_info.value.code == "COMPANY_MERGE_INTEGRITY_ERROR"


@pytest.mark.parametrize(
    ("redirect_links", "expected_code"),
    [
        (20, "COMPANY_MERGED_SUBJECT"),
        (21, "COMPANY_MERGE_INTEGRITY_ERROR"),
    ],
)
def test_merge_bounds_target_redirect_traversal(
    monkeypatch: pytest.MonkeyPatch,
    redirect_links: int,
    expected_code: str,
) -> None:
    """Changing the redirect cap would either allow unbounded traversal or reject the documented limit."""
    with _isolated_company_schema(monkeypatch) as schema_cursor:
        source = _create_company(f"Task6 Hop Source {redirect_links}")
        chain_ids = [str(uuid4()) for _ in range(redirect_links + 1)]
        with schema_cursor() as (_, cur):
            for index, company_id in enumerate(chain_ids):
                cur.execute(
                    """
                    INSERT INTO companies (
                        id, legal_name, normalized_name, verification_status, identity_source
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        company_id,
                        f"Task6 Hop Target {redirect_links}-{index}",
                        f"task6 hop target {redirect_links}-{index}",
                        "pending_verification",
                        "manual",
                    ),
                )
            for company_id, next_company_id in zip(chain_ids, chain_ids[1:]):
                cur.execute(
                    "UPDATE companies SET merged_into_id = %s WHERE id = %s",
                    (next_company_id, company_id),
                )

        with pytest.raises(DomainError) as exc_info:
            merge_company(source["company_id"], _merge_input(chain_ids[0]), None, "admin")

    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    ("source_credit_code", "target_credit_code"),
    [
        ("911100007109250324", None),
        (None, "911100007109250324"),
        (None, None),
    ],
)
def test_merge_allows_compatible_one_sided_or_absent_credit_codes(
    monkeypatch: pytest.MonkeyPatch,
    source_credit_code: str | None,
    target_credit_code: str | None,
) -> None:
    """Rejecting a merge unless both rows have the same code would block incomplete identity cleanup."""
    with _isolated_company_schema(monkeypatch):
        source = _create_company("Task6 Compatible Credit Source", credit_code=source_credit_code)
        target = _create_company("Task6 Compatible Credit Target", credit_code=target_credit_code)

        result = merge_company(source["company_id"], _merge_input(target["company_id"]), None, "admin")

    assert result["merged"] is True


def test_merge_rejects_whitespace_only_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persisting a blank reason would leave an irreversible merge without an auditable rationale."""
    with _isolated_company_schema(monkeypatch):
        source = _create_company("Task6 Blank Reason Source")
        target = _create_company("Task6 Blank Reason Target")

        with pytest.raises(DomainError) as exc_info:
            merge_company(
                source["company_id"],
                _merge_input(target["company_id"], reason=" \t "),
                None,
                "admin",
            )

    assert exc_info.value.code == "COMPANY_MERGE_REASON_REQUIRED"
    assert exc_info.value.status_code == 422


def test_merge_strips_reason_in_merge_log_audit_and_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeping surrounding reason whitespace would create inconsistent audit and event facts."""
    with _isolated_company_schema(monkeypatch) as schema_cursor:
        source = _create_company("Task6 Trimmed Reason Source")
        target = _create_company("Task6 Trimmed Reason Target")

        merge_company(
            source["company_id"],
            _merge_input(target["company_id"], reason="  重复登记  "),
            None,
            "admin",
        )

        with schema_cursor() as (_, cur):
            cur.execute("SELECT reason FROM company_merge_log")
            assert cur.fetchone() == ("重复登记",)
            cur.execute("SELECT details FROM audit_logs WHERE action = 'company.merge'")
            assert cur.fetchone()[0]["reason"] == "重复登记"
            cur.execute("SELECT payload FROM outbox_events WHERE event_type = 'company.merged'")
            assert cur.fetchone()[0]["reason"] == "重复登记"


def test_merge_writes_snapshot_redirect_versions_audit_event_and_preserves_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial merge would lose auditability, event delivery, or aliases needed for canonical lookup."""
    with _isolated_company_schema(monkeypatch) as schema_cursor:
        source = _create_company(
            "Task6 Source Legal Name",
            aliases=[CompanyAliasInput(alias_name="Task6 Source Alias", alias_type="former_name")],
        )
        target = _create_company(
            "Task6 Target Legal Name",
            aliases=[CompanyAliasInput(alias_name="Task6 Target Alias", alias_type="short_name")],
        )
        with schema_cursor() as (_, cur):
            cur.execute(
                "SELECT id, updated_at FROM companies WHERE id = ANY(%s::uuid[]) ORDER BY id",
                ([source["company_id"], target["company_id"]],),
            )
            pre_merge_updated_at = dict(cur.fetchall())

        result = merge_company(source["company_id"], _merge_input(target["company_id"]), None, "admin")

        assert result == {
            "source_company_id": source["company_id"],
            "target_company_id": target["company_id"],
            "source_version": 2,
            "target_version": 2,
            "merged": True,
        }
        assert get_company(source["company_id"])["id"] == target["company_id"]
        resolution = search_identity("Task6 Source Alias")
        assert resolution["resolution"] == "exact"
        assert resolution["exact"]["company_id"] == target["company_id"]
        assert resolution["exact"]["redirected_from"] == source["company_id"]

        with schema_cursor() as (_, cur):
            cur.execute(
                "SELECT merged_into_id, identity_version FROM companies WHERE id = %s",
                (source["company_id"],),
            )
            assert cur.fetchone() == (target["company_id"], 2)
            cur.execute(
                "SELECT identity_version FROM companies WHERE id = %s", (target["company_id"],)
            )
            assert cur.fetchone() == (2,)
            cur.execute(
                "SELECT source_version, target_version, compensation_snapshot "
                "FROM company_merge_log WHERE source_company_id = %s",
                (source["company_id"],),
            )
            source_version, target_version, snapshot = cur.fetchone()
            assert (source_version, target_version) == (1, 1)
            assert snapshot["source"]["id"] == source["company_id"]
            assert snapshot["source"]["merged_into_id"] is None
            assert snapshot["source"]["updated_at"] == pre_merge_updated_at[source["company_id"]].isoformat()
            assert snapshot["target"]["id"] == target["company_id"]
            assert snapshot["target"]["updated_at"] == pre_merge_updated_at[target["company_id"]].isoformat()
            cur.execute(
                "SELECT company_id FROM company_aliases WHERE normalized_alias = %s",
                ("task6 source alias",),
            )
            assert cur.fetchone() == (source["company_id"],)
            cur.execute(
                "SELECT action, details FROM audit_logs "
                "WHERE resource_id = %s AND action = 'company.merge'",
                (source["company_id"],),
            )
            assert cur.fetchone() == (
                "company.merge",
                {
                    "source_company_id": source["company_id"],
                    "target_company_id": target["company_id"],
                    "operator_id": None,
                    "reason": "重复档案",
                    "source_version": 2,
                    "target_version": 2,
                },
            )
            cur.execute(
                "SELECT event_type, aggregate_id, schema_version, payload FROM outbox_events "
                "WHERE aggregate_id = %s AND event_type = 'company.merged'",
                (source["company_id"],),
            )
            event_type, aggregate_id, schema_version, payload = cur.fetchone()
            assert event_type == "company.merged"
            assert aggregate_id == source["company_id"]
            assert schema_version == 1
            assert payload == {
                "source_company_id": source["company_id"],
                "target_company_id": target["company_id"],
                "operator_id": None,
                "reason": "重复档案",
                "source_version": 2,
                "target_version": 2,
            }


def test_merge_rolls_back_log_redirect_versions_and_audit_when_event_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If event insertion fails, committing any merge fact would violate transactional outbox guarantees."""
    with _isolated_company_schema(monkeypatch) as schema_cursor:
        source = _create_company("Task6 Rollback Source")
        target = _create_company("Task6 Rollback Target")
        with schema_cursor() as (_, cur):
            cur.execute(
                "ALTER TABLE outbox_events ADD CONSTRAINT task6_reject_merge_event "
                "CHECK (event_type <> 'company.merged')"
            )

        with pytest.raises(CheckViolation):
            merge_company(source["company_id"], _merge_input(target["company_id"]), None, "admin")

        with schema_cursor() as (_, cur):
            cur.execute(
                "SELECT merged_into_id, identity_version FROM companies WHERE id = %s",
                (source["company_id"],),
            )
            assert cur.fetchone() == (None, 1)
            cur.execute(
                "SELECT identity_version FROM companies WHERE id = %s", (target["company_id"],)
            )
            assert cur.fetchone() == (1,)
            cur.execute("SELECT COUNT(*) FROM company_merge_log")
            assert cur.fetchone() == (0,)
            cur.execute("SELECT COUNT(*) FROM audit_logs WHERE action = 'company.merge'")
            assert cur.fetchone() == (0,)
            cur.execute("SELECT COUNT(*) FROM outbox_events WHERE event_type = 'company.merged'")
            assert cur.fetchone() == (0,)


def test_lock_companies_for_merge_uses_one_uuid_sorted_for_update_statement() -> None:
    """Splitting or unsorting the lock query would reintroduce opposite-order deadlock risk."""
    class RecordingCursor:
        description = [("id",)]

        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        def execute(self, query: str, parameters: tuple[object, ...]) -> None:
            self.calls.append((query, parameters))

        def fetchall(self) -> list[tuple[str]]:
            return [(SOURCE_ID,), (TARGET_ID,)]

    cursor = RecordingCursor()
    rows = company_repo.lock_companies_for_merge(cursor, [TARGET_ID, SOURCE_ID])

    assert rows == [{"id": SOURCE_ID}, {"id": TARGET_ID}]
    assert len(cursor.calls) == 1
    query, parameters = cursor.calls[0]
    assert "ORDER BY id" in query
    assert "FOR UPDATE" in query
    assert parameters == ([SOURCE_ID, TARGET_ID],)


def test_opposite_merge_lock_requests_wait_then_complete_without_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opposite lock requests must wait on the same first row instead of forming a deadlock cycle."""
    with _isolated_company_schema(monkeypatch):
        source = _create_company("Task6 Lock Source")
        target = _create_company("Task6 Lock Target")
        first_locked = Event()
        release_first = Event()
        second_attempting_lock = Event()
        second_finished = Event()
        second_backend_pid: list[int] = []
        first_result: list[list[dict]] = []
        second_result: list[list[dict]] = []
        failures: list[BaseException] = []

        def lock_first() -> None:
            try:
                with company_service.get_cursor() as (_, cur):
                    first_result.append(
                        company_repo.lock_companies_for_merge(
                            cur, [target["company_id"], source["company_id"]]
                        )
                    )
                    first_locked.set()
                    assert release_first.wait(timeout=5)
            except BaseException as exc:
                failures.append(exc)

        def lock_second() -> None:
            try:
                with company_service.get_cursor() as (_, cur):
                    cur.execute("SELECT pg_backend_pid()")
                    second_backend_pid.append(cur.fetchone()[0])
                    second_attempting_lock.set()
                    second_result.append(
                        company_repo.lock_companies_for_merge(
                            cur, [source["company_id"], target["company_id"]]
                        )
                    )
                    second_finished.set()
            except BaseException as exc:
                failures.append(exc)

        first_thread = Thread(target=lock_first)
        second_thread = Thread(target=lock_second)
        first_thread.start()
        assert first_locked.wait(timeout=5)
        second_thread.start()
        assert second_attempting_lock.wait(timeout=5)
        try:
            deadline = monotonic() + 5
            observed_lock_wait = False
            observer_conn = get_conn()
            try:
                while monotonic() < deadline:
                    with observer_conn.cursor() as observer_cur:
                        observer_cur.execute(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                            (second_backend_pid[0],),
                        )
                        observed_lock_wait = observer_cur.fetchone() == ("Lock",)
                    observer_conn.rollback()
                    if observed_lock_wait:
                        break
                    if second_finished.wait(timeout=0.02):
                        break
            finally:
                put_conn(observer_conn)
            assert observed_lock_wait is True
            assert second_finished.is_set() is False
        finally:
            release_first.set()
            first_thread.join(timeout=5)
            second_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert failures == []
    assert not any(isinstance(failure, DeadlockDetected) for failure in failures)
    assert [[row["id"] for row in rows] for rows in first_result + second_result] == [
        sorted([source["company_id"], target["company_id"]]),
        sorted([source["company_id"], target["company_id"]]),
    ]
