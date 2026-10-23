import os
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domains.alert import case_access, case_repo, review_tasks


def _task(task_type: str = "review") -> dict:
    return {
        "id": "task-1",
        "monitor_target_id": "monitor-1",
        "task_type": task_type,
        "payload": {},
    }


def test_review_task_evidence_keeps_monitor_target_and_source_contract():
    evidence = review_tasks._evidence(
        _task(),
        dimension="risk_monitoring",
        provider="monitoring_workbench",
        source_type="risk_snapshot",
        status="partial",
        data_mode="formal",
        facts={"reason": "待补充资料"},
    )

    assert evidence["monitor_target_id"] == "monitor-1"
    assert evidence["entity_id"] == "monitor-1"
    assert evidence["source_type"] == "risk_snapshot"
    assert evidence["facts"]["reason"] == "待补充资料"


def test_verify_identity_task_fails_closed_to_needs_review(monkeypatch):
    target = {
        "company_name": "外部候选有限公司",
        "identity_status": "candidate",
        "target_type": "external_candidate",
    }
    status, result, evidence = review_tasks._execute_action(_task("verify_identity"), target)

    assert status == "needs_review"
    assert result["status"] == "needs_review"
    assert evidence[0]["dimension"] == "identity"
    assert evidence[0]["facts"]["identity_status"] == "candidate"


def test_assess_task_persists_snapshot_and_returns_supported_evidence(monkeypatch):
    assessment = SimpleNamespace(
        risk_score=23,
        risk_level="低风险",
        model_dump=lambda mode=None: {"risk_score": 23, "risk_level": "低风险"},
    )
    saved = []
    monkeypatch.setattr(
        "app.domains.risk.assessment_service.monitor_assessment",
        lambda *args, **kwargs: (saved.append((args,kwargs)) or assessment),
    )
    monkeypatch.setattr(
        "app.domains.alert.service.save_snapshot",
        lambda *args, **kwargs: saved.append((args, kwargs)),
    )

    status, result, evidence = review_tasks._execute_action(
        _task("assess"),
        {
            "company_name": "正式供应商有限公司",
            "target_type": "formal_supplier",
            "supplier_id": "supplier-1",
        },
    )

    assert status == "completed"
    assert result["risk_score"] == 23
    assert saved[0][1]["monitor_target_id"] == "monitor-1"
    assert evidence[0]["status"] == "supported"


def test_linked_task_uses_current_case_scope_for_manager_and_owner(monkeypatch):
    case = {
        "id": "case-1",
        "monitor_target_id": "monitor-1",
        "owner_user_id": "owner-1",
    }
    task = {**_task(), "requested_by": "requester-1"}
    monkeypatch.setattr(case_repo, "list_linked_cases_for_task", lambda _task_id: [case])
    monkeypatch.setattr(
        case_access,
        "case_capabilities",
        lambda _case, user_id, _role: {
            "can_read": user_id in {"manager-1", "owner-1"},
            "can_assign": user_id == "manager-1",
        },
    )

    review_tasks._authorize_case_task(task, "manager-1", "purchaser", action="decide")
    review_tasks._authorize_case_task(task, "owner-1", "purchaser", action="execute")
    with pytest.raises(PermissionError):
        review_tasks._authorize_case_task(task, "other-1", "purchaser", action="read")


def test_unlinked_task_keeps_requester_authorization(monkeypatch):
    monkeypatch.setattr(case_repo, "list_linked_cases_for_task", lambda _task_id: [])
    task = {**_task(), "requested_by": "buyer-1"}

    review_tasks._authorize_case_task(task, "buyer-1", "purchaser")
    with pytest.raises(PermissionError):
        review_tasks._authorize_case_task(task, "other-1", "purchaser")


@pytest.mark.parametrize("action", ["read", "decide", "execute"])
def test_unlinked_task_does_not_authorize_viewer_requester(monkeypatch, action):
    monkeypatch.setattr(case_repo, "list_linked_cases_for_task", lambda _task_id: [])
    task = {**_task(), "requested_by": "viewer-1"}

    with pytest.raises(PermissionError):
        review_tasks._authorize_case_task(task, "viewer-1", "viewer", action=action)


def test_list_review_tasks_includes_unlinked_tasks_for_current_manager_targets(monkeypatch):
    task = {
        "id": "task-legacy",
        "monitor_target_id": "monitor-1",
        "task_type": "review",
        "status": "completed",
        "requested_by": "former-buyer",
        "payload": {},
        "evidence_refs": [],
    }
    columns = list(task)

    class Cursor:
        description = [(column,) for column in columns]

        def execute(self, query, params):
            self.query = query
            self.params = params

        def fetchall(self):
            return [tuple(task[column] for column in columns)]

    cursor = Cursor()

    @contextmanager
    def cursor_context():
        yield None, cursor

    monkeypatch.setattr(review_tasks, "get_cursor", cursor_context)
    monkeypatch.setattr(case_access, "current_manager_departments", lambda _user_id: ["D1"])
    monkeypatch.setattr(
        review_tasks,
        "_current_user_target_ids",
        lambda _user_id, _user_role: ["monitor-1"],
        raising=False,
    )
    monkeypatch.setattr(
        review_tasks,
        "_authorize_target_access",
        lambda *_args, **_kwargs: {"monitor_target_id": "monitor-1"},
    )
    monkeypatch.setattr(case_repo, "list_linked_cases_for_task", lambda _task_id: [])
    monkeypatch.setattr(
        case_access, "is_current_manager_for_target", lambda _target, _user_id: True
    )

    tasks = review_tasks.list_review_tasks(None, "manager-1", "purchaser")

    assert [item["id"] for item in tasks] == ["task-legacy"]
    assert "monitor_target_id = ANY(%s)" in cursor.query
    assert "monitor-1" in cursor.params[-1]


def test_unlinked_task_allows_current_target_manager_and_rechecks_revocation(monkeypatch):
    target = {"monitor_target_id": "monitor-1", "supplier_id": "supplier-1"}
    task = {**_task(), "requested_by": "buyer-1"}
    current = {"manager": True}
    monkeypatch.setattr(case_repo, "list_linked_cases_for_task", lambda _task_id: [])
    monkeypatch.setattr(
        review_tasks,
        "_authorize_target_access",
        lambda *_args, **_kwargs: target,
    )
    monkeypatch.setattr(
        case_access,
        "is_current_manager_for_target",
        lambda _target, user_id: current["manager"] and user_id == "manager-1",
    )

    review_tasks._authorize_task(task, "manager-1", "purchaser", action="read")
    review_tasks._authorize_task(task, "manager-1", "purchaser", action="decide")
    review_tasks._authorize_task(task, "manager-1", "purchaser", action="execute")
    with pytest.raises(PermissionError):
        review_tasks._authorize_task(task, "manager-1", "viewer", action="read")

    current["manager"] = False
    for action in ("read", "decide", "execute"):
        with pytest.raises(PermissionError):
            review_tasks._authorize_task(task, "manager-1", "purchaser", action=action)


def test_supplement_task_reloads_current_coverage_instead_of_stale_payload(monkeypatch):
    monkeypatch.setattr(
        "app.domains.alert.service.get_watchlist_target_summaries",
        lambda actor_id, actor_role: [
            {
                "monitor_target_id": "monitor-1",
                "data_coverage": {
                    "status": "partial",
                    "missing_dimensions": ["当前内部交易"],
                    "dimensions": [{"key": "transaction", "status": "missing"}],
                },
            }
        ],
    )

    status, result, evidence = review_tasks._execute_action(
        {**_task("supplement_data"), "payload": {"data_coverage": {"missing_dimensions": ["旧值"]}}},
        {"company_name": "正式供应商"},
        actor_id="buyer-1",
        actor_role="purchaser",
    )

    assert status == "needs_review"
    assert result["missing_dimensions"] == ["当前内部交易"]
    assert result["missing_dimensions"] != ["旧值"]
    assert set(result) == {"summary", "status", "missing_dimensions"}
    assert evidence[0]["facts"]["coverage_status"] == "partial"


def test_supplement_task_fails_closed_when_current_coverage_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        "app.domains.alert.service.get_watchlist_target_summaries", lambda *_args: []
    )

    status, result, evidence = review_tasks._execute_action(
        {**_task("supplement_data"), "payload": {"data_coverage": {"missing_dimensions": ["旧值"]}}},
        {"company_name": "正式供应商"},
        actor_id="buyer-1",
        actor_role="purchaser",
    )

    assert status == "needs_review"
    assert "暂不可用" in result["summary"]
    assert result["missing_dimensions"] == []
    assert evidence[0]["facts"]["missing_dimensions"] == []


def test_case_task_reuses_active_execution_across_cases(monkeypatch):
    class Cursor:
        def __init__(self):
            self.query = ""
            self.queries = []

        def execute(self, query, _params=()):
            self.query = query
            self.queries.append(query)

        def fetchone(self):
            return ("existing-task",)

    cursor = Cursor()
    existing = {**_task(), "requested_by": "other-buyer"}
    monkeypatch.setattr(review_tasks, "_fetch_task", lambda *_args: existing)

    task = review_tasks.create_review_task_with_cursor(
        cursor,
        {
            "monitor_target_id": "monitor-1",
            "task_type": "review",
            "payload": {},
            "requested_by": "buyer-1",
            "user_role": "purchaser",
            "dedupe_across_requesters": True,
        },
    )

    assert task["id"] == "task-1"
    assert "requested_by = %s" not in cursor.query
    assert "pg_advisory_xact_lock" in cursor.queries[0]
    assert "SELECT id FROM monitor_review_tasks" in cursor.queries[1]


@pytest.mark.integration
def test_postgres_concurrent_task_creation_deduplicates_active_action(monkeypatch):
    test_database = os.environ.get("SUPPLISENSE_TEST_PG_DB")
    if not test_database:
        pytest.skip("SUPPLISENSE_TEST_PG_DB 未设置，跳过真实 PostgreSQL 验收")
    if test_database.lower() in {"sra", "tianyancha"}:
        pytest.fail("禁止在共享开发库上运行采购事项集成测试")

    from app.core.config import settings
    from app.db.init_pg import ensure_pg_schema
    from app.db.postgres import close_pool, get_cursor

    if test_database == settings.PG_DB:
        pytest.fail("测试数据库必须显式覆盖当前开发库 PG_DB")
    monkeypatch.setattr(settings, "PG_DB", test_database)
    close_pool()
    ensure_pg_schema()

    target_id = f"review-task-test-{uuid4()}"
    requester_ids = [str(uuid4()), str(uuid4())]
    barrier = Barrier(2)

    def create_task(requester_id: str) -> dict:
        barrier.wait(timeout=10)
        with get_cursor() as (_, cur):
            return review_tasks.create_review_task_with_cursor(
                cur,
                {
                    "monitor_target_id": target_id,
                    "task_type": "review",
                    "payload": {},
                    "requested_by": requester_id,
                    "user_role": "purchaser",
                    "dedupe_across_requesters": True,
                },
            )

    try:
        with get_cursor() as (_, cur):
            for user_id in requester_ids:
                cur.execute(
                    "INSERT INTO users(id,username,email,password_hash,role) VALUES(%s,%s,%s,'test','purchaser')",
                    (user_id, user_id, user_id + "@test.invalid"),
                )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(create_task, user_id) for user_id in requester_ids]
            created = [future.result(timeout=20) for future in futures]
        assert created[0]["id"] == created[1]["id"]
        with get_cursor() as (_, cur):
            cur.execute(
                "SELECT count(*) FROM monitor_review_tasks WHERE monitor_target_id = %s AND task_type = 'review' AND status IN ('pending_approval','approved','executing')",
                (target_id,),
            )
            assert cur.fetchone()[0] == 1
    finally:
        with get_cursor() as (_, cur):
            cur.execute("DELETE FROM monitor_review_tasks WHERE monitor_target_id = %s", (target_id,))
            cur.execute("DELETE FROM users WHERE id = ANY(%s::uuid[])", (requester_ids,))
        close_pool()
