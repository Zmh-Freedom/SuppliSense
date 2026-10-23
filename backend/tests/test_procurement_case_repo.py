from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.db.init_pg import DDL_STATEMENTS, INDEX_STATEMENTS
from app.domains.alert import case_repo


def _request_hash(values: dict) -> str:
    request_hash = getattr(case_repo, "_request_hash", None)
    assert callable(request_hash), "_request_hash must be implemented"
    return request_hash(values)


def test_request_hash_is_canonical_and_payload_sensitive():
    left = {"title": "复核", "scope": {"department": "D1", "target": "T1"}}
    right = {"scope": {"target": "T1", "department": "D1"}, "title": "复核"}

    assert _request_hash(left) == _request_hash(right)
    assert _request_hash(left) != _request_hash({**right, "title": "改动"})


class _RequestCursor:
    def __init__(self, *, inserted_id: str | None, existing: tuple | None) -> None:
        self.inserted_id = inserted_id
        self.existing = existing
        self.row: tuple | None = None
        self.queries: list[str] = []

    def execute(self, query: str, _params: tuple = ()) -> None:
        self.queries.append(query)
        if "INSERT INTO procurement_case_requests" in query:
            self.row = (self.inserted_id,) if self.inserted_id else None
        elif "SELECT id, request_hash, response" in query:
            self.row = self.existing
        else:
            self.row = None

    def fetchone(self) -> tuple | None:
        row, self.row = self.row, None
        return row


def test_same_idempotency_key_replays_stored_response():
    request_id = str(uuid4())
    request_hash = "same-hash"
    response = {"id": "case-1", "status": "open", "version": 1}
    cursor = _RequestCursor(
        inserted_id=None,
        existing=(request_id, request_hash, response),
    )
    begin_request = getattr(case_repo, "_begin_request", None)
    assert callable(begin_request), "_begin_request must be implemented"

    reserved_id, replay = begin_request(
        cursor, "actor-1", "request-1", {"title": "case"}, request_hash=request_hash
    )

    assert reserved_id == request_id
    assert replay == response
    assert len(cursor.queries) == 2


def test_same_idempotency_key_with_different_payload_conflicts():
    cursor = _RequestCursor(
        inserted_id=None,
        existing=(str(uuid4()), "prior-hash", {"id": "case-1"}),
    )
    begin_request = getattr(case_repo, "_begin_request", None)
    assert callable(begin_request), "_begin_request must be implemented"

    with pytest.raises(ValueError, match="同一幂等键"):
        begin_request(
            cursor, "actor-1", "request-1", {"title": "changed"}, request_hash="new-hash"
        )


class _TransitionCursor:
    columns = [
        ("id",),
        ("monitor_target_id",),
        ("department_code",),
        ("status",),
        ("version",),
        ("updated_at",),
        ("closed_at",),
    ]

    def __init__(self, rows: list[tuple]) -> None:
        self.rows = list(rows)
        self.row: tuple | None = None
        self.queries: list[str] = []
        self.description = self.columns

    def execute(self, query: str, _params: tuple = ()) -> None:
        self.queries.append(query)
        self.row = self.rows.pop(0) if query.lstrip().startswith(("SELECT", "UPDATE")) else None

    def fetchone(self) -> tuple | None:
        row, self.row = self.row, None
        return row


def _transition_rows(version: int = 1) -> list[tuple]:
    now = datetime.now(timezone.utc)
    current = ("case-1", "target-1", "D1", "in_progress", version, now, None)
    updated = ("case-1", "target-1", "D1", "in_progress", version + 1, now, None)
    return [current, updated]


def test_transition_uses_version_cas_and_appends_event_on_same_cursor():
    cursor = _TransitionCursor(_transition_rows())
    transition = getattr(case_repo, "apply_case_transition", None)
    assert callable(transition), "apply_case_transition must be implemented"

    result = transition(
        "case-1",
        1,
        {"status": "in_progress"},
        {
            "actor_id": "buyer-1",
            "event_type": "receipt_added",
            "from_status": "in_progress",
            "to_status": "in_progress",
            "payload": {"summary": "已复核"},
        },
        cur=cursor,
    )

    assert result["version"] == 2
    assert "FOR UPDATE" in cursor.queries[0]
    assert "WHERE id = %s AND version = %s" in cursor.queries[1]
    assert "INSERT INTO procurement_case_events" in cursor.queries[2]


def test_transition_rejects_stale_version_before_writing():
    cursor = _TransitionCursor(_transition_rows(version=2)[:1])
    transition = getattr(case_repo, "apply_case_transition", None)
    assert callable(transition), "apply_case_transition must be implemented"

    result = transition(
        "case-1",
        1,
        {"status": "deferred"},
        {"actor_id": "buyer-1", "event_type": "deferred"},
        cur=cursor,
    )

    assert result is None
    assert len(cursor.queries) == 1


def test_event_only_transition_increments_version_and_appends_event():
    cursor = _TransitionCursor(_transition_rows())
    transition = getattr(case_repo, "apply_case_transition", None)
    assert callable(transition), "apply_case_transition must be implemented"

    result = transition(
        "case-1",
        1,
        {},
        {
            "actor_id": "buyer-1",
            "event_type": "transfer_requested",
            "from_status": "in_progress",
            "to_status": "in_progress",
            "payload": {"reason": "请经理协调"},
        },
        cur=cursor,
    )

    assert result["version"] == 2
    assert result["event_id"]
    assert "version = version + 1" in cursor.queries[1]
    assert "INSERT INTO procurement_case_events" in cursor.queries[2]


def test_case_schema_has_active_uniqueness_and_append_only_events():
    cases_ddl = next(sql for sql in DDL_STATEMENTS if "CREATE TABLE IF NOT EXISTS procurement_cases" in sql)
    events_ddl = next(sql for sql in DDL_STATEMENTS if "CREATE TABLE IF NOT EXISTS procurement_case_events" in sql)
    tasks_ddl = next(sql for sql in DDL_STATEMENTS if "CREATE TABLE IF NOT EXISTS procurement_case_tasks" in sql)
    requests_ddl = next(sql for sql in DDL_STATEMENTS if "CREATE TABLE IF NOT EXISTS procurement_case_requests" in sql)
    active_index = next(sql for sql in INDEX_STATEMENTS if "idx_procurement_cases_active_trigger" in sql)

    assert "CHECK (case_type IN ('risk_review', 'coverage_gap', 'identity_review'))" in cases_ddl
    assert "CHECK (priority IN ('high', 'normal'))" in cases_ddl
    assert "UNIQUE (case_id, version)" in events_ddl
    assert "UNIQUE (case_id, task_id)" in tasks_ddl
    assert "UNIQUE (actor_id, request_key)" in requests_ddl
    assert "case_id UUID REFERENCES procurement_cases(id) ON DELETE RESTRICT" in requests_ddl
    assert "WHERE status <> 'closed'" in active_index


def test_case_list_applies_actor_scope_and_keyset_before_page_limit():
    class ListCursor:
        description = [("id",), ("updated_at",)]

        def __init__(self) -> None:
            self.query = ""
            self.params: tuple = ()

        def execute(self, query: str, params: tuple = ()) -> None:
            self.query = query
            self.params = params

        def fetchall(self) -> list[tuple]:
            return []

    cursor = ListCursor()
    result = case_repo.list_cases(
        {"status": "open"},
        actor_id="manager-1",
        user_role="purchaser",
        manager_departments=["D1", "D2"],
        limit=20,
        cursor={"updated_at": "2026-10-22T00:00:00+00:00", "id": "case-1"},
        cur=cursor,
    )

    assert result == {"items": [], "next_cursor": None}
    assert "created_by = %s" in cursor.query
    assert "owner_user_id = %s" in cursor.query
    assert "department_code = ANY(%s)" in cursor.query
    assert "updated_at < %s OR (updated_at = %s AND id > %s)" in cursor.query
    assert cursor.query.index("WHERE") < cursor.query.index("LIMIT %s")
    assert "ORDER BY updated_at DESC, id ASC" in cursor.query
    assert cursor.params[-1] == 21


@pytest.mark.parametrize(
    ("view", "required_scope", "forbidden_scope"),
    [
        ("mine", "created_by = %s", "department_code = ANY(%s)"),
        ("department", "department_code = ANY(%s)", "created_by = %s"),
    ],
)
def test_case_list_applies_selected_view_scope_in_sql(view, required_scope, forbidden_scope):
    class ListCursor:
        description = [("id",), ("updated_at",)]

        def __init__(self) -> None:
            self.query = ""
            self.params: tuple = ()

        def execute(self, query: str, params: tuple = ()) -> None:
            self.query = query
            self.params = params

        def fetchall(self) -> list[tuple]:
            return []

    cursor = ListCursor()
    case_repo.list_cases(
        {"view": view},
        actor_id="buyer-1",
        user_role="purchaser",
        manager_departments=["D1"],
        cur=cursor,
    )

    assert required_scope in cursor.query
    assert forbidden_scope not in cursor.query
    assert cursor.query.index("WHERE") < cursor.query.index("LIMIT %s")


def test_department_case_list_is_empty_without_current_manager_departments():
    class ListCursor:
        description = [("id",), ("updated_at",)]

        def __init__(self) -> None:
            self.query = ""
            self.params: tuple = ()

        def execute(self, query: str, params: tuple = ()) -> None:
            self.query = query
            self.params = params

        def fetchall(self) -> list[tuple]:
            return []

    cursor = ListCursor()
    case_repo.list_cases(
        {"view": "department"},
        actor_id="buyer-1",
        user_role="purchaser",
        manager_departments=[],
        cur=cursor,
    )

    assert "WHERE FALSE" in cursor.query


def _case_values(target_id: str, actor_id: str, trigger_key: str = "trigger-1") -> dict:
    return {
        "monitor_target_id": target_id,
        "company_id": None,
        "department_code": "D1",
        "case_type": "coverage_gap",
        "scope_key": f"{target_id}:D1",
        "trigger_key": trigger_key,
        "title": "覆盖缺口复核",
        "priority": "normal",
        "status": "open",
        "created_by": actor_id,
        "owner_user_id": None,
        "due_at": None,
        "next_review_at": None,
    }


@pytest.fixture
def isolated_pg(monkeypatch: pytest.MonkeyPatch):
    test_database = os.environ.get("SUPPLISENSE_TEST_PG_DB")
    if not test_database:
        pytest.skip("SUPPLISENSE_TEST_PG_DB 未设置，跳过真实 PostgreSQL 验收")
    if test_database.lower() in {"sra", "tianyancha"}:
        pytest.fail("禁止在共享开发库上运行采购事项集成测试")

    from app.core.config import settings
    from app.db.init_pg import ensure_pg_schema
    from app.db.postgres import close_pool

    if test_database == settings.PG_DB:
        pytest.fail("测试数据库必须显式覆盖当前开发库 PG_DB")
    monkeypatch.setattr(settings, "PG_DB", test_database)
    close_pool()
    ensure_pg_schema()
    yield
    close_pool()


@pytest.fixture
def case_identity(isolated_pg):
    del isolated_pg
    target_id = f"procurement-case-test-{uuid4()}"
    actor_id = str(uuid4())
    yield target_id, actor_id

    from app.db.postgres import get_cursor

    with get_cursor() as (_, cur):
        cur.execute("DELETE FROM procurement_case_requests WHERE actor_id = %s", (actor_id,))
        cur.execute("DELETE FROM procurement_cases WHERE monitor_target_id = %s", (target_id,))


@pytest.mark.integration
def test_postgres_concurrent_create_and_idempotency(case_identity):
    target_id, actor_id = case_identity
    values = _case_values(target_id, actor_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(case_repo.create_or_get_case, values, actor_id, "request-a")
        second = executor.submit(case_repo.create_or_get_case, values, actor_id, "request-b")
        first_case, second_case = first.result(timeout=10), second.result(timeout=10)

    assert first_case["id"] == second_case["id"]
    replay = case_repo.create_or_get_case(values, actor_id, "request-a")
    assert replay["id"] == first_case["id"]
    with pytest.raises(ValueError, match="同一幂等键"):
        case_repo.create_or_get_case({**values, "title": "payload changed"}, actor_id, "request-a")


@pytest.mark.integration
def test_postgres_closed_trigger_creates_related_case(case_identity):
    target_id, actor_id = case_identity
    first = case_repo.create_or_get_case(_case_values(target_id, actor_id), actor_id, "request-a")

    from app.db.postgres import get_cursor

    with get_cursor() as (_, cur):
        closed = case_repo.apply_case_transition(
            first["id"],
            first["version"],
            {"status": "closed", "closed_at": datetime.now(timezone.utc)},
            {
                "actor_id": actor_id,
                "event_type": "closed_for_test",
                "from_status": "open",
                "to_status": "closed",
                "payload": {},
            },
            cur=cur,
        )
        assert closed is not None

    second = case_repo.create_or_get_case(_case_values(target_id, actor_id), actor_id, "request-b")
    assert second["id"] != first["id"]
    assert second["reopened_from_case_id"] == first["id"]


@pytest.mark.integration
def test_postgres_case_and_request_roll_back_when_task_link_fails(case_identity):
    target_id, actor_id = case_identity

    from psycopg2.errors import ForeignKeyViolation
    from app.db.postgres import get_cursor

    case_id = None
    with pytest.raises(ForeignKeyViolation):
        with get_cursor() as (_, cur):
            case = case_repo.create_or_get_case(
                _case_values(target_id, actor_id), actor_id, "request-link", cur=cur
            )
            case_id = case["id"]
            case_repo.link_case_task(case_id, str(uuid4()), actor_id, cur=cur)

    assert case_id is not None
    assert case_repo.get_case(case_id) is None


@pytest.mark.integration
def test_postgres_event_failure_rolls_back_case_transition(case_identity):
    target_id, actor_id = case_identity
    created = case_repo.create_or_get_case(_case_values(target_id, actor_id), actor_id, "request-event")

    from psycopg2.errors import CheckViolation
    from app.db.postgres import get_cursor

    with pytest.raises(CheckViolation):
        with get_cursor() as (_, cur):
            case_repo.apply_case_transition(
                created["id"],
                created["version"],
                {"status": "in_progress"},
                {
                    "actor_id": actor_id,
                    "event_type": "",
                    "from_status": "open",
                    "to_status": "in_progress",
                    "payload": {},
                },
                cur=cur,
            )

    persisted = case_repo.get_case(created["id"])
    assert persisted is not None
    assert persisted["status"] == "open"
    assert persisted["version"] == 1


@pytest.mark.integration
def test_postgres_case_schema_ddl_is_repeatable(isolated_pg):
    del isolated_pg
    from app.db.init_pg import ensure_pg_schema

    ensure_pg_schema()
