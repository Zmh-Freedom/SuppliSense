"""Independent delivery ownership and terminal invalidation contracts."""

import pytest

from app.core.config import settings
from app.domains.outbox import service, worker


@pytest.fixture(autouse=True)
def limits(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_ROLE_ENABLED", True)
    monkeypatch.setattr(settings, "OUTBOX_WORKER_ENABLED", True)
    monkeypatch.setattr(worker.repo, "get_pending_stats", lambda: {"pending": 0, "oldest_age_seconds": None})


def test_api_relinquishes_delivery_but_keeps_backlog_metrics(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_ROLE_ENABLED", False)
    monkeypatch.setattr(worker, "process_outbox_batch", lambda **kw: pytest.fail("API must not claim"))
    assert worker.run_outbox_once()["claimed"] == 0


def test_dedicated_worker_refuses_api_owned_delivery(monkeypatch):
    from app.workers.delivery import run_delivery_once
    monkeypatch.setattr(worker, "process_outbox_batch", lambda **kw: pytest.fail("must fail before claim"))
    with pytest.raises(ValueError, match="DELIVERY_ROLE_ENABLED"):
        run_delivery_once("delivery-a")


def test_dedicated_worker_reuses_outbox_limits_and_global_pause(monkeypatch):
    from app.workers.delivery import run_delivery_once
    monkeypatch.setattr(settings, "DELIVERY_ROLE_ENABLED", False)
    calls = []
    monkeypatch.setattr(worker, "process_outbox_batch", lambda **kw: calls.append(kw) or {"claimed": 1, "published": 1, "failed": 0})
    assert run_delivery_once("delivery-a")["published"] == 1
    assert calls[0]["worker_id"] == "delivery-a"
    monkeypatch.setattr(settings, "OUTBOX_WORKER_ENABLED", False)
    assert run_delivery_once("delivery-b")["claimed"] == 0
    assert len(calls) == 1


@pytest.mark.parametrize("event_type,hint", [
    ("company.updated", "company_updated"), ("agent.chat.completed", "agent_run_updated"),
    ("risk.assessment.committed", "assessment_updated"), ("agent.action.approved", "action_updated"),
])
def test_only_winning_publication_emits_minimal_invalidation(monkeypatch, event_type, hint):
    from app.services import realtime
    event = {"event_id": "e", "event_type": event_type, "attempt_count": 0, "payload": {"secret": "private"}}
    monkeypatch.setattr(service, "is_rollout_frozen", lambda s: False)
    monkeypatch.setattr(service, "require_v2_execution", lambda s: None)
    monkeypatch.setattr(service, "_CONSUMERS", {(event_type, service.V2_ACTION_CONSUMER_NAME): lambda e: None})
    monkeypatch.setattr(service.repo, "claim_events", lambda *args: [event])
    monkeypatch.setattr(service.repo, "is_consumed", lambda *args: False)
    monkeypatch.setattr(service.repo, "record_consumption", lambda *args: True)
    monkeypatch.setattr(service, "notify_sourcing_risk_action_outcome", lambda *args: None)
    calls = []
    monkeypatch.setattr(realtime, "publish", lambda *args: calls.append(args))
    monkeypatch.setattr(service.repo, "mark_published", lambda *args: True)
    assert service.process_outbox_batch("d", 1, 3, 30)["published"] == 1
    assert calls == [(hint, {})]
    monkeypatch.setattr(service.repo, "mark_published", lambda *args: False)
    calls.clear()
    assert service.process_outbox_batch("d", 1, 3, 30)["published"] == 0
    assert calls == []


def test_failed_delivery_never_emits_success_hint(monkeypatch):
    from app.services import realtime
    monkeypatch.setattr(service.repo, "claim_events", lambda *a: [{"event_id": "e", "event_type": "unknown", "attempt_count": 0}])
    monkeypatch.setattr(service.repo, "mark_failed", lambda *a: True)
    monkeypatch.setattr(realtime, "publish", lambda *args: pytest.fail("not successful"))
    assert service.process_outbox_batch("d", 1, 3, 30)["failed"] == 1


def test_existing_targeted_alerts_use_shared_bus(monkeypatch):
    from app.domains.alert.notifier import _broadcast
    from app.services import realtime
    calls = []
    monkeypatch.setattr(realtime, "publish", lambda *args: calls.append(args))
    _broadcast("risk_alert", {"company": "private"}, {"buyer"})
    _broadcast("risk_alert", {"company": "private"}, set())
    assert calls == [("risk_alert", {"company": "private"}, {"buyer"})]
