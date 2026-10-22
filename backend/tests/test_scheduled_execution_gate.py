"""Permission, budget and unknown-result gates for scheduled execution."""

from uuid import uuid4

import pytest

from app.core.config import settings
from app.domains.scheduling import executor
from app.domains.scheduling.errors import ExecutionDenied
from app.domains.scheduling.periods import ScheduleDefinition


def _job(kind="financial_check", actor_kind="user") -> dict:
    actor_id = str(uuid4()) if actor_kind == "user" else "reports-service"
    definition = ScheduleDefinition(
        schedule_id="s1", revision=1, target_id="t1", cron="0 9 * * *",
        timezone="Asia/Shanghai", actor_kind=actor_kind, actor_id=actor_id,
        kind=kind, scope={"target_id": "t1"}, effective_at=__import__("datetime").datetime(
            2026, 10, 20, tzinfo=__import__("datetime").timezone.utc
        ),
    )
    return {
        "id": str(uuid4()), "schedule_id": "s1", "schedule_revision": 1,
        "target_id": "t1", "logical_period": "2026-10-22T09:00", "kind": kind,
        "actor_kind": actor_kind, "actor_id": actor_id, "fencing_token": 1,
        "definition": definition.to_payload(),
    }


@pytest.fixture(autouse=True)
def _lease_valid(monkeypatch):
    monkeypatch.setattr(executor.repo, "assert_scheduled_lease", lambda *a: None)
    monkeypatch.setattr(executor.repo, "mark_provider_started", lambda *a: None)


def _capture_finish(monkeypatch):
    finished = []

    def finish(job, worker_id, status, *, result=None, error=None):
        finished.append({"status": status, "result": result, "error": error})
        return True

    monkeypatch.setattr(executor.repo, "finish_scheduled_job", finish)
    return finished


def test_inactive_actor_is_denied_without_calling_handler(monkeypatch):
    called = []
    monkeypatch.setattr(executor, "_target_for", lambda _d: (_ for _ in ()).throw(ExecutionDenied("actor_inactive")))
    monkeypatch.setitem(executor.HANDLERS, "financial_check", lambda *a: called.append(1))
    finished = _capture_finish(monkeypatch)
    result = executor.execute_scheduled_job(_job(), "w1")
    assert result["denied"] is True
    assert called == []
    assert finished[0]["status"] == "CANCELLED"
    assert finished[0]["error"] == "actor_inactive"


def test_revoked_target_is_denied_without_calling_handler(monkeypatch):
    called = []
    monkeypatch.setattr(executor, "_target_for", lambda _d: (_ for _ in ()).throw(ExecutionDenied("target_revoked")))
    monkeypatch.setitem(executor.HANDLERS, "financial_check", lambda *a: called.append(1))
    finished = _capture_finish(monkeypatch)
    executor.execute_scheduled_job(_job(), "w1")
    assert called == []
    assert finished[0]["error"] == "target_revoked"


def test_unlisted_service_principal_is_denied(monkeypatch):
    called = []
    monkeypatch.setattr(executor.settings, "SCHEDULER_SERVICE_PRINCIPALS", "another-service")
    monkeypatch.setattr(
        "app.domains.alert.service.get_watchlist_targets",
        lambda *a, **k: called.append("targets") or [],
    )
    monkeypatch.setitem(executor.HANDLERS, "financial_check", lambda *a: called.append("handler"))
    finished = _capture_finish(monkeypatch)
    executor.execute_scheduled_job(_job(actor_kind="service"), "w1")
    assert called == []
    assert finished[0]["error"] == "service_principal_not_allowed"


def test_paid_kind_stays_blocked_when_switch_is_off(monkeypatch):
    called = []
    monkeypatch.setattr(executor.settings, "SCHEDULER_PAID_EXECUTION_ENABLED", False)
    monkeypatch.setattr(executor, "_target_for", lambda _d: {"monitor_target_id": "t1"})
    monkeypatch.setitem(executor.HANDLERS, "full_refresh", lambda *a: called.append("handler"))
    finished = _capture_finish(monkeypatch)
    executor.execute_scheduled_job(_job("full_refresh"), "w1")
    assert called == []
    assert finished[0]["error"] == "paid_execution_disabled"


def test_quota_exhaustion_stops_before_the_provider(monkeypatch):
    from app.core.paid_quota import PaidQuotaExceeded

    called = []
    monkeypatch.setattr(executor.settings, "SCHEDULER_PAID_EXECUTION_ENABLED", True)
    monkeypatch.setattr(executor, "_target_for", lambda _d: {"monitor_target_id": "t1"})
    monkeypatch.setattr(
        "app.core.paid_quota.reserve_paid_refresh_once",
        lambda *a: (_ for _ in ()).throw(PaidQuotaExceeded("full")),
    )
    monkeypatch.setitem(executor.HANDLERS, "full_refresh", lambda *a: called.append("handler"))
    finished = _capture_finish(monkeypatch)
    executor.execute_scheduled_job(_job("full_refresh"), "w1")
    assert called == []
    assert finished[0]["error"] == "paid_quota_exceeded"


def test_retry_reuses_existing_reservation(monkeypatch):
    calls = []
    monkeypatch.setattr(executor.settings, "SCHEDULER_PAID_EXECUTION_ENABLED", True)
    monkeypatch.setattr(executor, "_target_for", lambda _d: {"monitor_target_id": "t1"})
    monkeypatch.setattr("app.core.paid_quota.reserve_paid_refresh_once", lambda *a: False)
    monkeypatch.setitem(executor.HANDLERS, "full_refresh", lambda *a: calls.append("handler") or {"ok": True})
    finished = _capture_finish(monkeypatch)
    result = executor.execute_scheduled_job(_job("full_refresh"), "w1")
    assert calls == ["handler"]
    assert result["budget_reserved"] is False
    assert finished[0]["status"] == "SUCCEEDED"


def test_paid_handler_failure_requires_reconciliation(monkeypatch):
    monkeypatch.setattr(executor.settings, "SCHEDULER_PAID_EXECUTION_ENABLED", True)
    monkeypatch.setattr(executor, "_target_for", lambda _d: {"monitor_target_id": "t1"})
    monkeypatch.setattr("app.core.paid_quota.reserve_paid_refresh_once", lambda *a: True)
    monkeypatch.setitem(executor.HANDLERS, "full_refresh", lambda *a: (_ for _ in ()).throw(RuntimeError("provider timeout")))
    finished = _capture_finish(monkeypatch)
    result = executor.execute_scheduled_job(_job("full_refresh"), "w1")
    assert result["status"] == "NEEDS_REVIEW"
    assert finished[0]["error"] == "provider timeout"


def test_free_handler_failure_is_plain_failure(monkeypatch):
    monkeypatch.setattr(executor, "_target_for", lambda _d: {"monitor_target_id": "t1"})
    monkeypatch.setitem(executor.HANDLERS, "financial_check", lambda *a: (_ for _ in ()).throw(RuntimeError("akshare down")))
    finished = _capture_finish(monkeypatch)
    result = executor.execute_scheduled_job(_job(), "w1")
    assert result["status"] == "FAILED"
    assert finished[0]["status"] == "FAILED"


def test_unsupported_kind_is_blocked_without_any_external_lookup(monkeypatch):
    looked_up = []
    monkeypatch.setattr(executor, "_target_for", lambda _d: looked_up.append(1) or {})
    finished = _capture_finish(monkeypatch)
    result = executor.execute_scheduled_job(_job("alert_notify"), "w1")
    assert looked_up == []
    assert result["blocked"] is True
    assert finished[0]["status"] == "BLOCKED_POLICY"


def test_lease_loss_before_dispatch_stops_the_handler(monkeypatch):
    called = []
    monkeypatch.setattr(
        executor.repo, "assert_scheduled_lease",
        lambda *a: (_ for _ in ()).throw(ExecutionDenied("lease_lost")),
    )
    monkeypatch.setattr(executor, "_target_for", lambda _d: {"monitor_target_id": "t1"})
    monkeypatch.setitem(executor.HANDLERS, "financial_check", lambda *a: called.append(1))
    finished = _capture_finish(monkeypatch)
    executor.execute_scheduled_job(_job(), "w1")
    assert called == []
    assert finished[0]["error"] == "lease_lost"


def test_paid_provider_boundary_is_recorded_before_the_handler(monkeypatch):
    order = []
    monkeypatch.setattr(executor.settings, "SCHEDULER_PAID_EXECUTION_ENABLED", True)
    monkeypatch.setattr(executor, "_target_for", lambda _d: {"monitor_target_id": "t1"})
    monkeypatch.setattr("app.core.paid_quota.reserve_paid_refresh_once", lambda *a: True)
    monkeypatch.setattr(executor.repo, "mark_provider_started", lambda *a: order.append("boundary"))
    monkeypatch.setitem(executor.HANDLERS, "full_refresh", lambda *a: order.append("handler") or {"ok": True})
    _capture_finish(monkeypatch)
    executor.execute_scheduled_job(_job("full_refresh"), "w1")
    assert order == ["boundary", "handler"]


def test_free_job_does_not_mark_a_provider_boundary(monkeypatch):
    order = []
    monkeypatch.setattr(executor, "_target_for", lambda _d: {"monitor_target_id": "t1"})
    monkeypatch.setattr(executor.repo, "mark_provider_started", lambda *a: order.append("boundary"))
    monkeypatch.setitem(executor.HANDLERS, "financial_check", lambda *a: order.append("handler") or {})
    _capture_finish(monkeypatch)
    executor.execute_scheduled_job(_job(), "w1")
    assert order == ["handler"]


def test_success_after_fenced_commit_is_reported_for_review(monkeypatch):
    monkeypatch.setattr(executor, "_target_for", lambda _d: {"monitor_target_id": "t1"})
    monkeypatch.setitem(executor.HANDLERS, "financial_check", lambda *a: {"ok": True})
    monkeypatch.setattr(executor.repo, "finish_scheduled_job", lambda *a, **k: False)
    result = executor.execute_scheduled_job(_job(), "w1")
    assert result["terminal"] is False
    assert result["needs_review"] is True
    assert result["reason"] == "lease_lost_after_success"


def test_corrupt_definition_requires_review_instead_of_stranding_the_lease(monkeypatch):
    finished = _capture_finish(monkeypatch)
    job = _job()
    job["definition"] = {}
    result = executor.execute_scheduled_job(job, "w1")
    assert result["needs_review"] is True
    assert finished[0]["status"] == "NEEDS_REVIEW"
    assert finished[0]["error"].startswith("invalid_definition:")
