from types import SimpleNamespace

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
        query = ""

        def execute(self, query, _params=()):
            self.query = query

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
