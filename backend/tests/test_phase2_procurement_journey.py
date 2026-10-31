from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.domains.alert import api_workbench, case_repo, workbench_service

_fixture_spec = importlib.util.spec_from_file_location(
    "phase2_case_e2e_support",
    Path(__file__).with_name("test_procurement_case_e2e.py"),
)
if _fixture_spec is None or _fixture_spec.loader is None:
    raise ImportError("无法加载隔离的采购事项 E2E 工厂")
_fixture_module = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(_fixture_module)
_case_journey_fixture = _fixture_module.procurement_case_e2e


pytestmark = pytest.mark.agent_e2e
NOW = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def procurement_journey(monkeypatch: pytest.MonkeyPatch):
    client, store, context = _case_journey_fixture.__wrapped__(monkeypatch)
    app = client.app
    app.include_router(api_workbench.router, prefix="/api/v1")
    target = context["target"]

    def list_workbench_cases(filters, *, actor_id, user_role, target_ids, cur=None):
        del user_role, cur
        rows = [
            case for case in store.cases.values()
            if case["status"] != "closed"
            and case["monitor_target_id"] in target_ids
        ]
        if filters["view"] == "mine":
            rows = [
                case for case in rows
                if case["created_by"] == actor_id or case.get("owner_user_id") == actor_id
            ]
        else:
            rows = [case for case in rows if case.get("department_code") == filters["department_code"]]
        return sorted(rows, key=lambda case: (case["updated_at"], case["id"]), reverse=True)

    monkeypatch.setattr(case_repo, "list_workbench_cases", list_workbench_cases)
    monkeypatch.setattr(
        workbench_service,
        "_visible_targets",
        lambda actor_id, _role, department_code=None: (
            [target]
            if actor_id in {"buyer-1", "manager-1"}
            and (department_code is None or department_code == "D1")
            else []
        ),
    )
    monkeypatch.setattr(
        workbench_service,
        "_coverage_for_targets",
        lambda targets, _actor, _role, *, now: [
            (target_row["monitor_target_id"], {
                "status": "partial",
                "missing_dimensions": ["内部交易"],
                "dimensions": [{"key": "transaction", "status": "pending", "validity": {"status": "unknown"}}],
            })
            for target_row in targets
        ],
    )
    return client, store, context


def test_workbench_case_journey_independent_closure_keeps_unknown_coverage(
    procurement_journey,
) -> None:
    client, _store, context = procurement_journey
    use_actor = context["use_actor"]
    use_actor("buyer-1")

    created = client.post(
        "/api/v1/procurement-cases",
        headers={"Idempotency-Key": "phase2-queue-create"},
        json={
            "monitor_target_id": "monitor-e2e-1",
            "case_type": "coverage_gap",
            "department_code": "D1",
            "title": "处理内部交易覆盖缺口",
            "priority": "high",
        },
    )
    assert created.status_code == 201
    case_id = created.json()["case"]["id"]

    mine = client.get("/api/v1/procurement-workbench?view=mine")
    assert mine.status_code == 200
    assert mine.json()["counts"]["high"] == 1
    assert mine.json()["items"][0]["case_id"] == case_id
    assert mine.json()["items"][0]["detail_url"] == f"/cases/{case_id}"
    assert mine.json()["coverage"]["unknown_count"] == 1

    claimed = client.post(
        f"/api/v1/procurement-cases/{case_id}/commands",
        headers={"Idempotency-Key": "phase2-queue-claim"},
        json={"command": "claim", "expected_version": 1},
    )
    assert claimed.status_code == 200
    receipt = client.post(
        f"/api/v1/procurement-cases/{case_id}/commands",
        headers={"Idempotency-Key": "phase2-queue-receipt"},
        json={
            "command": "receipt",
            "expected_version": 2,
            "summary": "已核对现有来源，内部交易数据仍不可用。",
            "refs": ["monitoring://coverage/monitor-e2e-1"],
        },
    )
    assert receipt.status_code == 200
    requested = client.post(
        f"/api/v1/procurement-cases/{case_id}/commands",
        headers={"Idempotency-Key": "phase2-queue-close-request"},
        json={
            "command": "request_closure",
            "expected_version": 3,
            "category": "accepted_limitation",
            "summary": "现有数据已核对，保留资料限制记录。",
            "receipt_ids": [receipt.json()["event_id"]],
            "residual_limitations": "内部交易来源仍未覆盖，风险结论继续待核验。",
        },
    )
    assert requested.status_code == 200

    use_actor("manager-1")
    department = client.get("/api/v1/procurement-workbench?view=department&department_code=D1")
    assert department.status_code == 200
    assert department.json()["counts"]["pending_closure"] == 1
    assert department.json()["items"][0]["group"] == "pending_closure"

    decided = client.post(
        f"/api/v1/procurement-cases/{case_id}/commands",
        headers={"Idempotency-Key": "phase2-queue-approve"},
        json={
            "command": "approve_closure",
            "expected_version": 4,
            "reason": "独立审核通过，保留未覆盖事实。",
        },
    )
    assert decided.status_code == 200
    assert decided.json()["case"]["status"] == "closed"

    refreshed = client.get("/api/v1/procurement-workbench?view=department&department_code=D1")
    assert refreshed.status_code == 200
    assert refreshed.json()["items"] == []
    assert refreshed.json()["sections"]["cases"]["count"] == 0
    assert refreshed.json()["coverage"]["unknown_count"] == 1
    assert refreshed.json()["coverage"]["target_total"] == 1


@pytest.fixture
def isolated_workbench_pg(monkeypatch: pytest.MonkeyPatch):
    test_database = os.environ.get("SUPPLISENSE_TEST_PG_DB")
    if not test_database:
        pytest.skip("SUPPLISENSE_TEST_PG_DB 未设置，跳过真实 PostgreSQL 工作台验收")
    if test_database.lower() in {"sra", "tianyancha"}:
        pytest.fail("禁止在共享开发库上运行采购工作台集成测试")

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


@pytest.mark.integration
def test_postgres_workbench_groups_count_all_rows_and_pages_stably(
    isolated_workbench_pg, monkeypatch: pytest.MonkeyPatch
) -> None:
    del isolated_workbench_pg
    from app.db.postgres import get_cursor

    actor_id = str(uuid4())
    target_id = f"phase2-workbench-{uuid4()}"
    case_ids = [str(uuid4()) for _ in range(25)]
    updated_at = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
    try:
        with get_cursor() as (_, cur):
            for index, case_id in enumerate(case_ids):
                due_at = datetime(2026, 10, 23, 7, 59, tzinfo=timezone.utc) if index < 3 else None
                priority = "high" if 3 <= index < 5 else "normal"
                cur.execute(
                    """
                    INSERT INTO procurement_cases
                        (id, monitor_target_id, department_code, case_type, scope_key,
                         trigger_key, title, priority, status, created_by, owner_user_id,
                         due_at, updated_at)
                    VALUES (%s, %s, 'D1', 'coverage_gap', %s, %s, %s, %s,
                            'open', %s, NULL, %s, %s)
                    """,
                    (case_id, target_id, f"{target_id}:scope-{index}", f"trigger-{index}",
                     f"工作台测试事项 {index}", priority, actor_id, due_at, updated_at),
                )

        monkeypatch.setattr(workbench_service.case_access, "current_manager_departments", lambda _actor: [])
        monkeypatch.setattr(
            workbench_service,
            "_visible_targets",
            lambda *_args, **_kwargs: [{"monitor_target_id": target_id, "display_name": "PG 隔离对象"}],
        )
        monkeypatch.setattr(
            workbench_service.case_service,
            "_can_read_case_for_workbench",
            lambda *_args: {
                "can_read": True, "can_claim": True, "can_assign": False, "can_update": True,
                "can_request_closure": True, "can_decide_closure": False, "can_reopen": False,
            },
        )
        monkeypatch.setattr(workbench_service, "_coverage_for_targets", lambda *_args, **_kwargs: [])

        first = workbench_service.get_procurement_workbench(actor_id, "purchaser", now=NOW, limit=10)
        second = workbench_service.get_procurement_workbench(
            actor_id, "purchaser", now=NOW, limit=10, cursor=first["next_cursor"]
        )
        third = workbench_service.get_procurement_workbench(
            actor_id, "purchaser", now=NOW, limit=10, cursor=second["next_cursor"]
        )

        assert first["counts"] == {"pending_closure": 0, "overdue": 3, "due_review": 0, "high": 2, "normal": 20}
        assert len(first["items"]) == len(second["items"]) == 10
        assert len(third["items"]) == 5
        all_ids = [item["case_id"] for page in (first, second, third) for item in page["items"]]
        assert len(all_ids) == len(set(all_ids)) == 25
        assert [item["group"] for item in first["items"][:3]] == ["overdue"] * 3
        assert [item["group"] for item in first["items"][3:5]] == ["high"] * 2
    finally:
        with get_cursor() as (_, cur):
            cur.execute("DELETE FROM procurement_cases WHERE id = ANY(%s::uuid[])", (case_ids,))
