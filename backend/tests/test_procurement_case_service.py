from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domains.alert import case_service


NOW = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)


def _plan(case: dict, command: dict, *, capabilities: dict | None = None, **kwargs):
    planner = getattr(case_service, "_plan_case_command", None)
    assert callable(planner), "_plan_case_command must be implemented"
    return planner(
        case,
        command,
        "buyer-1",
        "purchaser",
        capabilities or {"can_read": True, "can_update": True},
        now=NOW,
        **kwargs,
    )


def _case(**updates) -> dict:
    return {
        "id": "case-1",
        "monitor_target_id": "target-1",
        "department_code": "D1",
        "case_type": "coverage_gap",
        "scope_key": "target-1:D1:coverage_gap",
        "created_by": "buyer-1",
        "owner_user_id": None,
        "status": "open",
        "version": 1,
        "closure_request": None,
        **updates,
    }


def test_case_list_reports_views_from_current_manager_scope(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def capture_list(filters, **kwargs):
        captured["filters"] = filters
        captured.update(kwargs)
        return {"items": [], "next_cursor": None}

    monkeypatch.setattr(case_service.case_access, "current_manager_departments", lambda _user_id: ["D1"])
    monkeypatch.setattr(case_service.case_repo, "list_cases", capture_list)

    result = case_service.list_cases({"view": "department"}, "buyer-1", "purchaser")

    assert result == {
        "items": [],
        "next_cursor": None,
        "available_views": {"mine": True, "department": True, "accessible": False},
    }
    assert captured["filters"]["view"] == "department"
    assert captured["manager_departments"] == ["D1"]


def test_case_list_rejects_unknown_view():
    with pytest.raises(ValueError, match="事项视图"):
        case_service.list_cases({"view": "all"}, "buyer-1", "purchaser")


def test_viewer_receives_no_available_case_list_views(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(case_service.case_access, "current_manager_departments", lambda _user_id: ["D1"])
    monkeypatch.setattr(
        case_service.case_repo,
        "list_cases",
        lambda *_args, **_kwargs: {"items": [], "next_cursor": None},
    )

    result = case_service.list_cases({"view": "mine"}, "viewer-1", "viewer")

    assert result["available_views"] == {"mine": False, "department": False, "accessible": False}


def test_admin_case_list_exposes_global_accessible_view(monkeypatch: pytest.MonkeyPatch):
    captured = {}
    monkeypatch.setattr(case_service.case_access, "current_manager_departments", lambda _user_id: [])
    monkeypatch.setattr(
        case_service.case_repo,
        "list_cases",
        lambda filters, **_kwargs: captured.update(filters=filters) or {
            "items": [],
            "next_cursor": None,
        },
    )

    result = case_service.list_cases({"view": "accessible"}, "admin-1", "admin")

    assert captured["filters"]["view"] == "accessible"
    assert result["available_views"] == {"mine": True, "department": False, "accessible": True}


def test_case_list_fills_authorized_page_and_keeps_cursor_after_last_visible_case(
    monkeypatch: pytest.MonkeyPatch,
):
    rows = [
        {"id": f"case-{letter}", "updated_at": NOW - timedelta(minutes=index)}
        for index, letter in enumerate("abcde")
    ]
    calls: list[dict | None] = []

    def page_candidates(_filters, **kwargs):
        cursor = kwargs["cursor"]
        calls.append(cursor)
        offset = next(
            (index + 1 for index, row in enumerate(rows) if cursor and row["id"] == cursor["id"]),
            0,
        )
        candidates = rows[offset : offset + 2]
        has_more = offset + len(candidates) < len(rows)
        next_cursor = (
            {"updated_at": candidates[-1]["updated_at"].isoformat(), "id": candidates[-1]["id"]}
            if has_more
            else None
        )
        return {"items": candidates, "next_cursor": next_cursor}

    monkeypatch.setattr(case_service.case_access, "current_manager_departments", lambda _user_id: ["D1"])
    monkeypatch.setattr(case_service.case_repo, "list_cases", page_candidates)
    monkeypatch.setattr(
        case_service,
        "_can_read_case",
        lambda row, _user_id, _role: {"can_read": row["id"] in {"case-c", "case-d", "case-e"}},
    )

    first = case_service.list_cases({"view": "department", "limit": 2}, "manager-1", "purchaser")
    assert [item["case"]["id"] for item in first["items"]] == ["case-c", "case-d"]
    first_cursor = case_service._decode_cursor(first["next_cursor"])
    assert first_cursor["id"] == "case-d"

    second = case_service.list_cases(
        {"view": "department", "limit": 2, "cursor": first["next_cursor"]},
        "manager-1",
        "purchaser",
    )
    assert [item["case"]["id"] for item in second["items"]] == ["case-e"]
    assert second["next_cursor"] is None
    assert len(calls) >= 4


def test_claim_is_limited_to_open_unassigned_case():
    changes, event = _plan(
        _case(),
        {"command": "claim", "expected_version": 1},
        capabilities={"can_read": True, "can_claim": True},
    )

    assert changes == {"status": "in_progress", "owner_user_id": "buyer-1"}
    assert event["event_type"] == "claimed"

    with pytest.raises((ValueError, PermissionError)):
        _plan(
            _case(owner_user_id="buyer-2"),
            {"command": "claim", "expected_version": 1},
            capabilities={"can_read": True, "can_claim": False},
        )


@pytest.mark.parametrize(
    ("case_status", "command"),
    [
        ("in_progress", {"command": "claim", "expected_version": 1}),
        ("closed", {"command": "assign", "expected_version": 1, "owner_user_id": "00000000-0000-0000-0000-000000000002"}),
        ("closed", {"command": "defer", "expected_version": 1, "reason": "等待资料", "next_review_at": NOW + timedelta(days=1)}),
        ("in_progress", {"command": "resume", "expected_version": 1}),
        ("open", {"command": "request_closure", "expected_version": 1, "category": "withdrawn", "summary": "撤回", "receipt_ids": ["receipt-1"]}),
        ("in_progress", {"command": "approve_closure", "expected_version": 1, "reason": "通过"}),
        ("in_progress", {"command": "reject_closure", "expected_version": 1, "reason": "驳回"}),
        ("in_progress", {"command": "reopen", "expected_version": 1, "reason": "重开"}),
    ],
)
def test_command_edges_reject_wrong_case_status(case_status, command):
    with pytest.raises(case_service.CaseConflictError):
        _plan(_case(status=case_status, owner_user_id="buyer-1"), command)


def test_transfer_request_records_reason_without_changing_owner_or_status():
    case = _case(status="in_progress", owner_user_id="buyer-1")
    changes, event = _plan(
        case,
        {"command": "request_transfer", "expected_version": 1, "reason": "转交财务组核对"},
    )

    assert changes == {}
    assert event["event_type"] == "transfer_requested"
    assert event["payload"]["reason"] == "转交财务组核对"
    assert case["owner_user_id"] == "buyer-1"
    assert case["status"] == "in_progress"


def test_defer_requires_reason_and_future_review_time():
    case = _case(status="in_progress", owner_user_id="buyer-1")
    with pytest.raises(ValueError):
        _plan(case, {"command": "defer", "expected_version": 1, "next_review_at": NOW + timedelta(days=1)})
    with pytest.raises(ValueError):
        _plan(
            case,
            {"command": "defer", "expected_version": 1, "reason": "等待资料", "next_review_at": NOW},
        )

    changes, _ = _plan(
        case,
        {
            "command": "defer",
            "expected_version": 1,
            "reason": "等待资料",
            "next_review_at": NOW + timedelta(days=1),
        },
    )
    assert changes["status"] == "deferred"


def test_closure_request_requires_existing_receipt_and_limitations():
    validator = getattr(case_service, "_validate_closure_request", None)
    assert callable(validator), "_validate_closure_request must be implemented"
    case = _case(status="in_progress", owner_user_id="buyer-1")
    receipts = [{"id": "receipt-1", "event_type": "receipt_recorded"}]

    request = validator(
        case,
        {
            "category": "accepted_limitation",
            "summary": "已联系数据源，当前仍无可用交易快照",
            "receipt_ids": ["receipt-1"],
            "residual_limitations": "内部交易数据仍未覆盖",
        },
        receipts,
    )
    assert request["category"] == "accepted_limitation"

    with pytest.raises(ValueError):
        validator(
            case,
            {
                "category": "accepted_limitation",
                "summary": "已处理",
                "receipt_ids": ["receipt-1"],
            },
            receipts,
        )
    with pytest.raises(ValueError):
        validator(
            case,
            {"category": "resolved", "summary": "已解决", "receipt_ids": ["missing"]},
            receipts,
        )


def test_resolved_coverage_requires_linked_current_evidence():
    validator = getattr(case_service, "_validate_resolved_closure", None)
    assert callable(validator), "_validate_resolved_closure must be implemented"
    case = _case()
    receipts = [
        {"id": "receipt-1", "event_type": "receipt_recorded", "payload": {"refs": ["snapshot-old"]}}
    ]

    with pytest.raises(ValueError):
        validator(case, {"category": "resolved", "receipt_ids": ["receipt-1"]}, receipts, set())
    with pytest.raises(ValueError):
        validator(
            case,
            {"category": "resolved", "receipt_ids": ["receipt-1"]},
            receipts,
            {
                "evidence_ids": {"snapshot-current"},
                "coverage_status": "complete",
                "missing_dimensions": [],
                "evidence_by_dimension": {"risk_snapshot": {"snapshot-current"}},
            },
        )

    current_receipts = [
        {
            "id": "receipt-1",
            "event_type": "receipt_recorded",
            "created_at": "2026-10-23T10:00:00+00:00",
            "payload": {"refs": ["transaction-current"]},
        }
    ]
    incomplete_coverage = {
        "evidence_ids": {"identity-company-id", "transaction-current"},
        "coverage_status": "partial",
        "missing_dimensions": ["内部交易"],
    }
    with pytest.raises(ValueError, match="覆盖仍不完整"):
        validator(
            case,
            {"category": "resolved", "receipt_ids": ["receipt-1"]},
            [
                {"id": "receipt-1", "event_type": "receipt_recorded", "payload": {"refs": ["identity-company-id"]}}
            ],
            incomplete_coverage,
        )
    with pytest.raises(ValueError, match="原缺口"):
        validator(
            case,
            {"category": "resolved", "receipt_ids": ["receipt-1"]},
            [
                {"id": "receipt-1", "event_type": "receipt_recorded", "payload": {"refs": ["identity-company-id"]}}
            ],
            {
                "evidence_ids": {"identity-company-id"},
                "coverage_status": "complete",
                "missing_dimensions": [],
                "evidence_by_dimension": {"identity": {"identity-company-id"}},
            },
            closure_baseline={
                "captured_at": NOW.isoformat(),
                "missing_dimension_keys": ["transaction"],
                "evidence_by_dimension": {"transaction": []},
            },
        )
    validator(
        case,
        {"category": "resolved", "receipt_ids": ["receipt-1"]},
        current_receipts,
        {
            "evidence_ids": {"transaction-current"},
            "coverage_status": "complete",
            "missing_dimensions": [],
            "evidence_by_dimension": {"transaction": {"transaction-current"}},
            "evidence_collected_at": {
                "transaction-current": "2026-10-23T09:00:00+00:00",
            },
        },
        closure_baseline={
            "captured_at": NOW.isoformat(),
            "missing_dimension_keys": ["transaction"],
            "evidence_by_dimension": {"transaction": []},
        },
    )


def test_current_evidence_snapshot_does_not_treat_company_id_as_coverage_evidence(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "app.domains.alert.service.get_watchlist_target_summaries",
        lambda *_args: [
            {
                "monitor_target_id": "target-1",
                "company_id": "company-1",
                "data_coverage": {
                    "status": "partial",
                    "missing_dimensions": ["内部交易"],
                    "dimensions": [
                        {
                            "key": "identity",
                            "status": "available",
                            "validity": {"facts": {"company_id": "company-1"}},
                        },
                        {
                            "key": "risk_snapshot",
                            "status": "available",
                            "validity": {
                                "collected_at": "2026-10-22T08:00:00+00:00",
                                "source_version": "risk-source-v1",
                            },
                            "facts": {
                                "assessment_id": "assessment-current",
                                "checked_at": "2026-10-22T09:00:00+00:00",
                            },
                        },
                        {
                            "key": "transaction",
                            "status": "missing",
                            "validity": {
                                "collected_at": "2026-10-20T08:00:00+00:00",
                                "source_version": "transaction-source-v1",
                            },
                            "facts": {"source_id": "transaction-old"},
                        },
                    ],
                },
            }
        ],
    )

    snapshot = case_service._current_evidence_snapshot(_case(), "buyer-1", "purchaser")

    assert snapshot["coverage_status"] == "partial"
    assert snapshot["missing_dimensions"] == ["内部交易"]
    assert snapshot["evidence_ids"] == {
        "assessment-current",
        "company-1",
        "risk-source-v1",
    }
    assert snapshot["evidence_by_dimension"] == {
        "identity": {"company-1"},
        "risk_snapshot": {"assessment-current", "risk-source-v1"},
    }
    assert snapshot["missing_dimension_keys"] == ["transaction"]
    assert snapshot["observed_evidence_by_dimension"]["transaction"] == {
        "transaction-old",
        "transaction-source-v1",
    }
    assert snapshot["evidence_collected_at"] == {
        "assessment-current": "2026-10-22T09:00:00+00:00",
        "risk-source-v1": "2026-10-22T08:00:00+00:00",
        "transaction-old": "2026-10-20T08:00:00+00:00",
        "transaction-source-v1": "2026-10-20T08:00:00+00:00",
    }


def test_create_coverage_gap_case_persists_initial_evidence_baseline(monkeypatch):
    captured: dict = {}
    snapshot = {
        "coverage_status": "partial",
        "missing_dimension_keys": ["transaction"],
        "observed_evidence_by_dimension": {"transaction": {"transaction-old"}},
        "evidence_collected_at": {"transaction-old": "2026-10-22T08:00:00+00:00"},
    }
    monkeypatch.setattr(
        case_service.case_access,
        "get_case_target",
        lambda _target_id: {"monitor_target_id": "target-1", "company_id": "company-1"},
    )
    monkeypatch.setattr(
        case_service.case_access,
        "resolve_case_scope",
        lambda *_args: {
            "target_id": "target-1",
            "department_code": "D1",
        },
    )
    monkeypatch.setattr(
        case_service, "_current_evidence_snapshot", lambda *_args: snapshot
    )

    def capture_create(
        values, actor_id, request_key, *, created_event_payload=None
    ):
        captured["created_event_payload"] = created_event_payload
        return {**values, "id": "case-1"}

    monkeypatch.setattr(case_service.case_repo, "create_or_get_case", capture_create)

    case_service.create_case(
        {
            "monitor_target_id": "target-1",
            "case_type": "coverage_gap",
            "title": "补齐内部交易覆盖",
        },
        "buyer-1",
        "purchaser",
        "create-coverage-case",
    )

    baseline = captured["created_event_payload"]["closure_baseline"]
    assert baseline["missing_dimension_keys"] == ["transaction"]
    assert baseline["evidence_by_dimension"] == {"transaction": ["transaction-old"]}
    assert baseline["captured_at"]


def test_coverage_gap_resolved_requires_new_evidence_for_original_missing_dimensions():
    validator = case_service._validate_resolved_closure
    baseline = {
        "captured_at": NOW.isoformat(),
        "missing_dimension_keys": ["transaction"],
        "evidence_by_dimension": {"transaction": ["transaction-old"]},
    }
    request = {"category": "resolved", "receipt_ids": ["receipt-1"]}
    current = {
        "evidence_ids": {"transaction-old", "transaction-new", "risk-new"},
        "coverage_status": "complete",
        "missing_dimensions": [],
        "evidence_by_dimension": {
            "transaction": {"transaction-old", "transaction-new"},
            "risk_snapshot": {"risk-new"},
        },
        "evidence_collected_at": {
            "transaction-old": "2026-10-20T08:00:00+00:00",
            "transaction-new": "2026-10-23T09:00:00+00:00",
            "risk-new": "2026-10-23T09:00:00+00:00",
        },
    }

    with pytest.raises(ValueError, match="原缺口"):
        validator(
            _case(),
            request,
            [
                {
                    "id": "receipt-1",
                    "event_type": "receipt_recorded",
                    "payload": {"refs": ["transaction-old"]},
                }
            ],
            current,
            closure_baseline=baseline,
        )

    with pytest.raises(ValueError, match="原缺口"):
        validator(
            _case(),
            request,
            [
                {
                    "id": "receipt-1",
                    "event_type": "receipt_recorded",
                    "payload": {"refs": ["risk-new"]},
                }
            ],
            current,
            closure_baseline=baseline,
        )

    validator(
        _case(),
        request,
        [
            {
                "id": "receipt-1",
                "event_type": "receipt_recorded",
                    "created_at": "2026-10-23T10:00:00+00:00",
                "payload": {"refs": ["transaction-new"]},
            }
        ],
        current,
        closure_baseline=baseline,
    )


def test_coverage_gap_receipt_must_follow_the_evidence_it_references():
    baseline = {
        "captured_at": "2026-10-22T08:00:00+00:00",
        "missing_dimension_keys": ["transaction"],
        "evidence_by_dimension": {"transaction": []},
    }
    request = {"category": "resolved", "receipt_ids": ["receipt-1"]}
    current = {
        "evidence_ids": {"transaction-new"},
        "coverage_status": "complete",
        "missing_dimensions": [],
        "evidence_by_dimension": {"transaction": {"transaction-new"}},
        "evidence_collected_at": {"transaction-new": "2026-10-23T09:00:00+00:00"},
    }

    with pytest.raises(ValueError, match="回执时间"):
        case_service._validate_resolved_closure(
            _case(),
            request,
            [
                {
                    "id": "receipt-1",
                    "event_type": "receipt_recorded",
                    "created_at": "2026-10-23T08:59:00+00:00",
                    "payload": {"refs": ["transaction-new"]},
                }
            ],
            current,
            closure_baseline=baseline,
        )


def test_coverage_gap_resolved_closure_rechecks_current_coverage_at_request_and_approval():
    receipt = {
        "id": "receipt-1",
        "event_type": "receipt_recorded",
        "payload": {"refs": ["transaction-current"]},
    }
    request = {
        "command": "request_closure",
        "expected_version": 1,
        "category": "resolved",
        "summary": "已补齐当前数据来源",
        "receipt_ids": ["receipt-1"],
    }
    incomplete_snapshot = {
        "evidence_ids": {"identity-company-id", "transaction-current"},
        "coverage_status": "partial",
        "missing_dimensions": ["内部交易"],
        "evidence_by_dimension": {"identity": {"identity-company-id"}, "transaction": {"transaction-current"}},
    }

    with pytest.raises(ValueError, match="覆盖仍不完整"):
        _plan(
            _case(status="in_progress", owner_user_id="buyer-1"),
            request,
            capabilities={"can_read": True, "can_request_closure": True},
            receipts=[receipt],
            current_evidence=incomplete_snapshot,
        )

    pending_case = _case(
        status="pending_closure",
        owner_user_id="buyer-1",
        closure_request={
            "requested_by": "buyer-2",
            "category": "resolved",
            "summary": "已补齐当前数据来源",
            "receipt_ids": ["receipt-1"],
        },
    )
    with pytest.raises(ValueError, match="覆盖仍不完整"):
        _plan(
            pending_case,
            {"command": "approve_closure", "expected_version": 1, "reason": "独立复核"},
            capabilities={"can_read": True, "can_decide_closure": True},
            receipts=[receipt],
            current_evidence=incomplete_snapshot,
        )


def test_coverage_gap_requires_new_creation_gap_evidence_at_request_and_approval():
    baseline = {
        "captured_at": "2026-10-22T08:00:00+00:00",
        "missing_dimension_keys": ["transaction"],
        "evidence_by_dimension": {"transaction": ["transaction-old"]},
    }
    receipt = {
        "id": "receipt-1",
        "event_type": "receipt_recorded",
        "payload": {"refs": ["transaction-old"]},
    }
    current_evidence = {
        "evidence_ids": {"transaction-old"},
        "coverage_status": "complete",
        "missing_dimensions": [],
        "evidence_by_dimension": {"transaction": {"transaction-old"}},
        "evidence_collected_at": {"transaction-old": "2026-10-20T08:00:00+00:00"},
    }
    request = {
        "command": "request_closure",
        "expected_version": 1,
        "category": "resolved",
        "summary": "引用现存交易快照",
        "receipt_ids": ["receipt-1"],
    }

    with pytest.raises(ValueError, match="原缺口"):
        _plan(
            _case(status="in_progress", owner_user_id="buyer-1"),
            request,
            capabilities={"can_read": True, "can_request_closure": True},
            receipts=[receipt],
            current_evidence=current_evidence,
            closure_baseline=baseline,
        )

    pending_case = _case(
        status="pending_closure",
        owner_user_id="buyer-1",
        closure_request={
            "requested_by": "buyer-2",
            "category": "resolved",
            "summary": "引用现存交易快照",
            "receipt_ids": ["receipt-1"],
        },
    )
    with pytest.raises(ValueError, match="原缺口"):
        _plan(
            pending_case,
            {"command": "approve_closure", "expected_version": 1, "reason": "复核"},
            capabilities={"can_read": True, "can_decide_closure": True},
            receipts=[receipt],
            current_evidence=current_evidence,
            closure_baseline=baseline,
        )


def test_closure_approval_requires_independent_reviewer():
    case = _case(
        status="pending_closure",
        owner_user_id="buyer-1",
        closure_request={
            "requested_by": "buyer-2",
            "category": "withdrawn",
            "summary": "已完成处理",
            "receipt_ids": ["receipt-1"],
        },
    )
    with pytest.raises(PermissionError):
        _plan(
            case,
            {"command": "approve_closure", "expected_version": 1, "reason": "确认"},
            capabilities={"can_read": True, "can_decide_closure": False},
        )

    changes, _ = _plan(
        case,
        {"command": "approve_closure", "expected_version": 1, "reason": "已独立复核"},
        capabilities={"can_read": True, "can_decide_closure": True},
        receipts=[{"id": "receipt-1", "event_type": "receipt_recorded", "payload": {"refs": []}}],
    )
    assert changes["status"] == "closed"
    assert changes["closed_at"] == NOW

    self_request = _case(
        status="pending_closure",
        closure_request={
            "requested_by": "buyer-1",
            "category": "withdrawn",
            "summary": "处理完成",
            "receipt_ids": ["receipt-1"],
        },
    )
    with pytest.raises(PermissionError):
        _plan(
            self_request,
            {"command": "approve_closure", "expected_version": 1, "reason": "自审"},
            capabilities={"can_read": True, "can_decide_closure": True},
            receipts=[{"id": "receipt-1", "event_type": "receipt_recorded", "payload": {}}],
        )


def test_reject_closure_returns_case_to_processing():
    case = _case(
        status="pending_closure",
        closure_request={"requested_by": "buyer-2", "category": "withdrawn"},
    )
    changes, _ = _plan(
        case,
        {"command": "reject_closure", "expected_version": 1, "reason": "仍需补充回执"},
        capabilities={"can_read": True, "can_decide_closure": True},
    )
    assert changes["status"] == "in_progress"


def test_duplicate_closure_requires_same_scope_and_acyclic_reference():
    validator = getattr(case_service, "_validate_duplicate_target", None)
    assert callable(validator), "_validate_duplicate_target must be implemented"
    case = _case()
    duplicate = _case(id="case-2")
    validator(case, duplicate, lambda _case_id: None)

    with pytest.raises(ValueError):
        validator(case, case, lambda _case_id: None)
    with pytest.raises(ValueError):
        validator(case, _case(id="case-3", scope_key="other-scope"), lambda _case_id: None)

    duplicate["closure_request"] = {"category": "duplicate", "duplicate_case_id": "case-1"}
    with pytest.raises(ValueError):
        validator(case, duplicate, lambda case_id: case if case_id == "case-1" else None)


def test_completed_review_task_does_not_close_business_case():
    planner = getattr(case_service, "_plan_case_command", None)
    assert callable(planner), "_plan_case_command must be implemented"
    changes, event = planner(
        _case(status="in_progress", owner_user_id="buyer-1"),
        {"command": "review_task_linked", "expected_version": 1, "task_id": "task-1"},
        "buyer-1",
        "purchaser",
        {"can_read": True, "can_update": True},
        now=NOW,
    )
    assert changes == {}
    assert event["event_type"] == "review_task_linked"
    assert event["to_status"] == "in_progress"


def test_command_validator_rejects_extra_and_irrelevant_fields():
    validator = getattr(case_service, "_validate_command", None)
    assert callable(validator), "_validate_command must be implemented"
    with pytest.raises(ValueError):
        validator({"command": "claim", "expected_version": 1, "owner_user_id": "buyer-2"})
    with pytest.raises(ValueError):
        validator({"command": "receipt", "expected_version": 1, "summary": "receipt", "refs": [], "extra": "x"})
