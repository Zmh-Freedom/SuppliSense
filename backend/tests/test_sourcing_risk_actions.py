"""Safety boundaries for approved sourcing-risk V2 actions."""

from contextlib import contextmanager
from unittest.mock import MagicMock, Mock

import pytest

from app.core.errors import DomainError
from app.db import mongo
from app.domains.agent_run.schemas import ApprovalDecisionRequest
from app.domains.outbox import service as outbox_service
from app.domains.sourcing_risk import action_service


RUN_ID = "00000000-0000-4000-8000-000000000801"
OTHER_RUN_ID = "00000000-0000-4000-8000-000000000802"
PROPOSAL_ID = "00000000-0000-4000-8000-000000000803"


@contextmanager
def _cursor():
    yield None, object()


def _run(*, status: str = "ACTION_PENDING", version: int = 3, user_id: str = "owner") -> dict:
    return {"id": RUN_ID, "status": status, "version": version, "user_id": user_id}


def _proposal(**overrides: object) -> dict:
    return {
        "id": PROPOSAL_ID,
        "run_id": RUN_ID,
        "action_type": "import_external_supplier",
        "status": "pending",
        "execution_state": "pending",
        "idempotency_key": "import:external:company-a",
        "payload": {"company_name": "外部企业 A", "risk_level": "low"},
        **overrides,
    }


def _approved(version: int = 3) -> ApprovalDecisionRequest:
    return ApprovalDecisionRequest(expected_version=version, decision="approved")


def test_unapproved_import_only_persists_proposal_without_outbox_or_master_write(monkeypatch):
    """Calling an import adapter while proposing would bypass human approval."""
    persisted: list[dict] = []
    enqueue = Mock()
    importer = Mock()
    monkeypatch.setattr(
        action_service,
        "insert_action_proposal",
        lambda **kwargs: persisted.append(kwargs) or _proposal(),
    )
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(action_service, "get_run_for_update", lambda *_: _run())
    monkeypatch.setattr(action_service, "get_action_proposal_for_idempotency_key", lambda *_: None)
    monkeypatch.setattr(action_service, "validate_action_proposal_ownership", lambda *_: None)
    monkeypatch.setattr(action_service, "enqueue_event", enqueue)
    monkeypatch.setattr(action_service, "import_external_supplier", importer)

    result = action_service.create_action_proposal(
        RUN_ID,
        "import_external_supplier",
        {"company_name": "外部企业 A", "risk_level": "low"},
        "import:external:company-a",
    )

    assert result["status"] == "pending"
    assert persisted[0]["action_type"] == "import_external_supplier"
    enqueue.assert_not_called()
    importer.assert_not_called()


def test_approval_enqueues_one_transactional_event_and_duplicate_replay_is_rejected(monkeypatch):
    """Dropping the pending-state gate could enqueue the same approved action twice."""
    decisions: list[dict] = []
    enqueued: list[dict] = []
    events: list[dict] = []
    state = _proposal()
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(action_service, "get_run_for_update", lambda *_: _run())
    monkeypatch.setattr(action_service, "get_run_for_user", lambda *_: _run())
    monkeypatch.setattr(action_service, "get_action_proposal_for_update", lambda *_: state)
    monkeypatch.setattr(
        action_service,
        "update_action_proposal",
        lambda _cur, _run_id, _proposal_id, **kwargs: state.update(kwargs) or state,
    )
    monkeypatch.setattr(
        action_service,
        "update_run_status",
        lambda _run_id, expected_version, status, **_: _run(status=status, version=expected_version + 1),
    )
    monkeypatch.setattr(
        action_service,
        "insert_approval_decision",
        lambda *args, **kwargs: decisions.append({"args": args, "kwargs": kwargs}) or {"id": "decision"},
    )
    monkeypatch.setattr(
        action_service,
        "append_event",
        lambda *args, **kwargs: events.append({"args": args, "kwargs": kwargs}) or {"event_id": 1},
    )
    monkeypatch.setattr(
        action_service,
        "enqueue_event",
        lambda *args, **kwargs: enqueued.append({"args": args, "kwargs": kwargs}) or "event-id",
    )

    first = action_service.decide_action_proposal(RUN_ID, PROPOSAL_ID, _approved(), "reviewer", "analyst")

    assert first["proposal"]["status"] == "approved"
    assert len(decisions) == 1
    assert len(enqueued) == 1
    assert enqueued[0]["args"][1] == "agent.action.approved"
    assert events[0]["args"][2] == "approval"

    with pytest.raises(DomainError) as exc:
        action_service.decide_action_proposal(RUN_ID, PROPOSAL_ID, _approved(), "reviewer", "analyst")

    assert exc.value.code == "AGENT_ACTION_ALREADY_DECIDED"
    assert len(enqueued) == 1


def test_approval_rejects_foreign_run_stale_version_and_unauthorized_reviewer(monkeypatch):
    """Weak authorization or ownership checks would let another user approve an action."""
    monkeypatch.setattr(action_service, "get_run_for_user", lambda *_: None)

    with pytest.raises(DomainError) as foreign:
        action_service.decide_action_proposal(RUN_ID, PROPOSAL_ID, _approved(), "viewer", "viewer")
    assert (foreign.value.code, foreign.value.status_code) == ("AGENT_ACTION_APPROVAL_FORBIDDEN", 403)

    monkeypatch.setattr(action_service, "get_run_for_user", lambda *_: _run(version=4))
    with pytest.raises(DomainError) as stale:
        action_service.decide_action_proposal(RUN_ID, PROPOSAL_ID, _approved(version=3), "owner", "analyst")
    assert stale.value.code == "AGENT_RUN_VERSION_CONFLICT"

    with pytest.raises(DomainError) as forbidden:
        action_service.decide_action_proposal(RUN_ID, PROPOSAL_ID, _approved(version=4), "owner", "viewer")
    assert forbidden.value.code == "AGENT_ACTION_APPROVAL_FORBIDDEN"


def test_approval_rejects_proposal_belonging_to_another_run(monkeypatch):
    """Selecting a proposal only by ID would permit cross-run approval substitution."""
    monkeypatch.setattr(action_service, "get_run_for_user", lambda *_: _run())
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(
        action_service,
        "get_action_proposal_for_update",
        lambda *_: _proposal(run_id=OTHER_RUN_ID),
    )

    with pytest.raises(DomainError) as exc:
        action_service.decide_action_proposal(RUN_ID, PROPOSAL_ID, _approved(), "reviewer", "analyst")

    assert exc.value.code == "AGENT_ACTION_PROPOSAL_NOT_FOUND"


def test_high_risk_import_rejects_self_approval(monkeypatch):
    """Removing the maker-checker rule would let a requester approve a high-risk import."""
    monkeypatch.setattr(action_service, "get_run_for_user", lambda *_: _run(user_id="owner"))
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(action_service, "get_action_proposal_for_update", lambda *_: _proposal(payload={"company_name": "外部企业 A", "risk_level": "high"}))

    with pytest.raises(DomainError) as exc:
        action_service.decide_action_proposal(RUN_ID, PROPOSAL_ID, _approved(), "owner", "analyst")

    assert exc.value.code == "AGENT_ACTION_SELF_APPROVAL_FORBIDDEN"


def test_proposal_rejects_candidate_or_company_from_another_run(monkeypatch):
    """Allowing foreign candidates would detach a business write from its reviewed Run."""
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(action_service, "get_run_for_update", lambda *_: _run())
    monkeypatch.setattr(action_service, "get_action_proposal_for_idempotency_key", lambda *_: None)
    monkeypatch.setattr(action_service, "validate_action_proposal_ownership", lambda *_: (_ for _ in ()).throw(DomainError("AGENT_ACTION_CANDIDATE_NOT_FOUND", "候选企业不属于任务", 404)))

    with pytest.raises(DomainError) as exc:
        action_service.create_action_proposal(
            RUN_ID,
            "import_external_supplier",
            {"company_name": "外部企业 A"},
            "import:external:foreign-candidate",
            candidate_id="foreign-candidate",
        )

    assert exc.value.code == "AGENT_ACTION_CANDIDATE_NOT_FOUND"


def test_unapproved_event_never_imports_and_approved_event_imports_once(monkeypatch):
    """Skipping durable approval/idempotency checks could import an external candidate twice."""
    importer = Mock()
    states = [_proposal(status="pending"), _proposal(status="approved"), _proposal(status="succeeded", execution_state="succeeded")]
    monkeypatch.setattr(action_service, "get_action_proposal_for_execution", lambda *_: states.pop(0))
    monkeypatch.setattr(action_service, "import_external_supplier", importer)
    monkeypatch.setattr(action_service, "mark_action_succeeded", lambda *_: None)
    monkeypatch.setattr(action_service, "append_action_status", lambda *_: None)

    with pytest.raises(DomainError) as exc:
        action_service.execute_sourcing_risk_action({"payload": {"run_id": RUN_ID, "proposal_id": PROPOSAL_ID}})
    assert exc.value.code == "AGENT_ACTION_NOT_APPROVED"

    action_service.execute_sourcing_risk_action({"payload": {"run_id": RUN_ID, "proposal_id": PROPOSAL_ID}})
    action_service.execute_sourcing_risk_action({"payload": {"run_id": RUN_ID, "proposal_id": PROPOSAL_ID}})

    importer.assert_called_once_with({**_proposal()["payload"], "idempotency_key": "import:external:company-a"})


def test_v2_action_dead_letters_after_five_attempts_and_only_then_marks_run_failed(monkeypatch):
    """Using the global retry limit or failing the run on a retry breaks V2 action recovery."""
    event = {"event_id": "event-1", "event_type": "agent.action.approved", "attempt_count": 4, "payload": {}}
    outcomes: list[str] = []
    monkeypatch.setattr(outbox_service.repo, "claim_events", lambda *_: [event])
    monkeypatch.setattr(outbox_service.repo, "is_consumed", lambda *_: False)
    monkeypatch.setattr(outbox_service.repo, "mark_failed", lambda *args: args[2] == 5)
    monkeypatch.setattr(
        outbox_service,
        "_CONSUMERS",
        {("agent.action.approved", "test-v2-action"): lambda _: (_ for _ in ()).throw(RuntimeError("adapter failed"))},
    )
    monkeypatch.setattr(
        outbox_service,
        "notify_sourcing_risk_action_outcome",
        lambda _event, outcome: outcomes.append(outcome),
    )

    result = outbox_service.process_outbox_batch("worker", 1, 99, 60)

    assert result == {"claimed": 1, "published": 0, "failed": 1}
    assert outcomes == ["dead_lettered"]


def test_action_side_effects_have_unique_durable_idempotency_keys(monkeypatch):
    """Removing these indexes could let concurrent outbox replays duplicate a business write."""
    db = MagicMock()
    monkeypatch.setattr(mongo, "get_db", lambda: db)

    mongo.ensure_indexes()

    indexed_collections = [call.args[0] for call in db.__getitem__.call_args_list]
    assert "suppliers" in indexed_collections
    assert "access_applications" in indexed_collections
    assert "agent_report_exports" in indexed_collections
    for collection_name in ("suppliers", "access_applications", "agent_report_exports"):
        calls = db[collection_name].create_index.call_args_list
        assert any(call.kwargs.get("unique") is True for call in calls)
