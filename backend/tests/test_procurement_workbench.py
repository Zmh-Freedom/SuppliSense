from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domains.alert import workbench_service
from app.domains.alert import case_repo


NOW = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)


def _case(case_id: str, **updates) -> dict:
    return {
        "id": case_id,
        "monitor_target_id": f"target-{case_id}",
        "department_code": "D1",
        "status": "in_progress",
        "priority": "normal",
        "owner_user_id": "buyer-1",
        "created_by": "buyer-1",
        "due_at": None,
        "next_review_at": None,
        "updated_at": NOW,
        "closure_request": None,
        **updates,
    }


def _caps(**updates) -> dict[str, bool]:
    return {
        "can_read": True,
        "can_claim": False,
        "can_assign": False,
        "can_update": True,
        "can_request_closure": True,
        "can_decide_closure": False,
        "can_reopen": False,
        **updates,
    }


def test_group_precedence_deadline_boundary_defer_and_closed_exclusion():
    classify = getattr(workbench_service, "_queue_group", None)
    assert callable(classify), "workbench must expose the queue grouping rule"

    cases = [
        (_case("approval", status="pending_closure", due_at=NOW - timedelta(days=3)), _caps(can_decide_closure=True)),
        (_case("overdue", due_at=NOW), _caps()),
        (_case("due-review", status="deferred", due_at=None, next_review_at=NOW), _caps()),
        (_case("high", priority="high", due_at=NOW + timedelta(days=2)), _caps()),
        (_case("normal", due_at=None), _caps()),
        (_case("closed", status="closed", priority="high"), _caps(can_reopen=True)),
        (_case("self-approval", status="pending_closure", closure_request={"requested_by": "buyer-1"}), _caps(can_update=False, can_request_closure=False)),
    ]

    groups = [classify(case, caps, now=NOW) for case, caps in cases]

    assert groups == ["pending_closure", "overdue", "due_review", "high", "normal", None, None]


def test_same_target_multiple_coverage_gaps_count_once_and_states_can_overlap():
    aggregate = getattr(workbench_service, "_aggregate_coverage", None)
    assert callable(aggregate), "workbench must aggregate coverage by distinct target"

    result = aggregate([
        ("target-1", {"dimensions": [
            {"validity": {"status": "unknown"}},
            {"validity": {"status": "stale"}},
            {"validity": {"status": "failed"}},
        ]}),
        ("target-2", {"dimensions": [
            {"validity": {"status": "unknown"}},
            {"validity": {"status": "unknown"}},
        ]}),
        ("target-3", {"dimensions": [
            {"validity": {"status": "available"}},
        ]}),
    ])

    assert result == {"target_total": 3, "unknown_count": 2, "stale_count": 1, "failed_count": 1}


def test_coverage_target_links_are_stable_and_group_multiple_gaps_once():
    links = workbench_service._coverage_target_items(
        [("target-1", {
            "status": "partial",
            "missing_dimensions": ["舆情", "财务"],
            "dimensions": [
                {"validity": {"status": "unknown"}},
                {"validity": {"status": "stale"}},
            ],
        })],
        [{"monitor_target_id": "target-1", "display_name": "甲供应商"}],
    )

    assert links == [{
        "target_id": "target-1",
        "display_name": "甲供应商",
        "missing_dimensions": ["舆情", "财务"],
        "unknown": True,
        "stale": True,
        "failed": False,
    }]


def test_service_filters_revoked_and_self_approval_before_counts_and_pagination(monkeypatch: pytest.MonkeyPatch):
    cases = [
        _case("visible", priority="high"),
        _case("revoked", priority="high", owner_user_id="other", created_by="other"),
        _case("self-approval", status="pending_closure", priority="high", closure_request={"requested_by": "buyer-1"}, can_decide_closure=False),
        _case("closed", status="closed", priority="high"),
    ]
    monkeypatch.setattr(workbench_service.case_access, "current_manager_departments", lambda _user_id: [])
    monkeypatch.setattr(workbench_service, "_visible_targets", lambda *_args, **_kwargs: [{"monitor_target_id": "target-visible", "company_name": "A"}])
    monkeypatch.setattr(workbench_service.case_repo, "list_workbench_cases", lambda *_args, **_kwargs: cases)
    monkeypatch.setattr(workbench_service.case_service, "_can_read_case_for_workbench", lambda case, *_args: _caps(can_read=case["id"] != "revoked", can_update=case["id"] not in {"self-approval", "closed"}, can_request_closure=case["id"] not in {"self-approval", "closed"}, can_decide_closure=False, can_reopen=case["id"] == "closed"))
    monkeypatch.setattr(workbench_service, "_coverage_for_targets", lambda *_args, **_kwargs: [])

    result = workbench_service.get_procurement_workbench("buyer-1", "purchaser", now=NOW, limit=1)

    assert result["counts"] == {"pending_closure": 0, "overdue": 0, "due_review": 0, "high": 1, "normal": 0}
    assert [item["case_id"] for item in result["items"]] == ["visible"]
    assert result["next_cursor"] is None


def test_admin_must_choose_department_and_manager_cannot_read_another_department(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(workbench_service.case_access, "current_manager_departments", lambda _user_id: ["D1"])
    monkeypatch.setattr(workbench_service, "_visible_targets", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(workbench_service.case_repo, "list_workbench_cases", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(workbench_service, "_coverage_for_targets", lambda *_args, **_kwargs: [])

    with pytest.raises(ValueError, match="管理员.*科室"):
        workbench_service.get_procurement_workbench("admin-1", "admin", view="department", now=NOW)

    with pytest.raises(PermissionError, match="当前管理科室"):
        workbench_service.get_procurement_workbench("manager-1", "purchaser", view="department", department_code="D2", now=NOW)


def test_coverage_failure_keeps_cases_available_and_cases_failure_does_not_report_zero(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(workbench_service.case_access, "current_manager_departments", lambda _user_id: [])
    monkeypatch.setattr(workbench_service, "_visible_targets", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(workbench_service.case_repo, "list_workbench_cases", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(workbench_service, "_coverage_for_targets", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("mongo down")))

    result = workbench_service.get_procurement_workbench("buyer-1", "purchaser", now=NOW)

    assert result["sections"]["cases"]["status"] == "available"
    assert result["sections"]["coverage"]["status"] == "unavailable"
    assert result["coverage"] is None

    monkeypatch.setattr(workbench_service.case_repo, "list_workbench_cases", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("pg down")))
    monkeypatch.setattr(workbench_service, "_coverage_for_targets", lambda *_args, **_kwargs: [])
    result = workbench_service.get_procurement_workbench("buyer-1", "purchaser", now=NOW)
    assert result["sections"]["cases"]["status"] == "unavailable"
    assert all(value is None for value in result["counts"].values())


def test_authorization_lookup_failure_marks_cases_section_unavailable(monkeypatch: pytest.MonkeyPatch):
    case = _case("candidate")
    monkeypatch.setattr(workbench_service.case_access, "current_manager_departments", lambda _user_id: [])
    monkeypatch.setattr(workbench_service, "_visible_targets", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(workbench_service.case_repo, "list_workbench_cases", lambda *_args, **_kwargs: [case])
    monkeypatch.setattr(workbench_service.case_access, "_target_for_case", lambda _target_id: {"monitor_target_id": "target-candidate", "supplier_id": "supplier-1"})
    monkeypatch.setattr(
        workbench_service.case_access,
        "_current_supplier_assignments",
        lambda _supplier_id: (_ for _ in ()).throw(RuntimeError("responsibility database unavailable")),
    )
    monkeypatch.setattr(workbench_service, "_coverage_for_targets", lambda *_args, **_kwargs: [])

    result = workbench_service.get_procurement_workbench("buyer-1", "purchaser", now=NOW)

    assert result["sections"]["cases"]["status"] == "unavailable"
    assert all(value is None for value in result["counts"].values())
    assert result["items"] == []
    assert result["next_cursor"] is None


def test_queue_cursor_keeps_priority_and_due_order_across_pages(monkeypatch: pytest.MonkeyPatch):
    cases = [
        _case("normal", priority="normal"),
        _case("high", priority="high"),
        _case("overdue", due_at=NOW - timedelta(hours=1)),
    ]
    monkeypatch.setattr(workbench_service.case_access, "current_manager_departments", lambda _user_id: [])
    monkeypatch.setattr(workbench_service, "_visible_targets", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(workbench_service.case_repo, "list_workbench_cases", lambda *_args, **_kwargs: cases)
    monkeypatch.setattr(workbench_service.case_service, "_can_read_case_for_workbench", lambda *_args: _caps())
    monkeypatch.setattr(workbench_service, "_coverage_for_targets", lambda *_args, **_kwargs: [])

    first = workbench_service.get_procurement_workbench("buyer-1", "purchaser", now=NOW, limit=2)
    second = workbench_service.get_procurement_workbench("buyer-1", "purchaser", now=NOW, limit=2, cursor=first["next_cursor"])

    assert [item["case_id"] for item in first["items"]] == ["overdue", "high"]
    assert [item["case_id"] for item in second["items"]] == ["normal"]
    assert second["next_cursor"] is None


def test_case_repository_scopes_target_department_and_actor_before_unbounded_read():
    class Cursor:
        description = [("id",)]

        def __init__(self) -> None:
            self.query = ""
            self.params = ()

        def execute(self, query: str, params: tuple) -> None:
            self.query = query
            self.params = params

        def fetchall(self) -> list[tuple]:
            return []

    cursor = Cursor()
    result = case_repo.list_workbench_cases(
        {"view": "department", "department_code": "D1"},
        actor_id="manager-1",
        user_role="purchaser",
        target_ids=["target-1", "target-2"],
        cur=cursor,
    )

    assert result == []
    assert "status <> 'closed'" in cursor.query
    assert "monitor_target_id = ANY(%s)" in cursor.query
    assert "department_code = %s" in cursor.query
    assert "LIMIT" not in cursor.query
    assert cursor.params == (["target-1", "target-2"], "D1")
