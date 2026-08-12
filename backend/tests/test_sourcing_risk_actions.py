"""Safety boundaries for approved sourcing-risk V2 actions."""

from contextlib import contextmanager
from unittest.mock import MagicMock, Mock

import pytest

from app.core.errors import DomainError
from app.db import mongo
from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_cursor
from app.domains.agent_run import repo as agent_run_repo
from app.domains.agent_run.schemas import ApprovalDecisionRequest
from app.domains.outbox import service as outbox_service
from app.domains.sourcing_risk import action_service


RUN_ID = "00000000-0000-4000-8000-000000000801"
OTHER_RUN_ID = "00000000-0000-4000-8000-000000000802"
PROPOSAL_ID = "00000000-0000-4000-8000-000000000803"


@pytest.fixture(autouse=True)
def enable_v2_action_control(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_RUN_V2_ROLLOUT", "default")


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


def _create_proposal(
    *,
    action_type: str = "import_external_supplier",
    payload: dict | None = None,
    idempotency_key: str = "import:external:company-a",
    candidate_id: str | None = "candidate-a",
    user_id: str = "owner",
    user_role: str = "analyst",
    expected_version: int = 3,
) -> dict:
    return action_service.create_action_proposal(
        RUN_ID,
        action_type,
        payload or {"company_name": "外部企业 A", "risk_level": "low"},
        idempotency_key,
        candidate_id=candidate_id,
        user_id=user_id,
        user_role=user_role,
        expected_version=expected_version,
    )


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
    monkeypatch.setattr(
        action_service,
        "get_action_candidate_for_update",
        lambda *_: {
            "id": "candidate-a",
            "run_id": RUN_ID,
            "source": "external",
            "status": "staged_candidate",
            "candidate_snapshot": {"company_name": "外部企业 A", "risk_level": "low"},
        },
        raising=False,
    )
    monkeypatch.setattr(action_service, "enqueue_event", enqueue)
    monkeypatch.setattr(action_service, "import_external_supplier", importer)

    result = _create_proposal()

    assert result["status"] == "pending"
    assert persisted[0]["action_type"] == "import_external_supplier"
    enqueue.assert_not_called()
    importer.assert_not_called()


def test_shadow_action_boundary_rejects_proposal_before_database_write(monkeypatch):
    """Shadow must be read-only even when the action service is called directly."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(action_service, "require_v2_execution", lambda *_: (_ for _ in ()).throw(
        DomainError("AGENT_RUN_V2_SHADOW_READ_ONLY", "Shadow 模式禁止执行领域写入", 409)
    ))
    monkeypatch.setattr(action_service, "get_cursor", lambda: (_ for _ in ()).throw(AssertionError("shadow must not write")))

    with pytest.raises(DomainError) as error:
        _create_proposal()
    assert error.value.code == "AGENT_RUN_V2_SHADOW_READ_ONLY"


@pytest.mark.parametrize("entrypoint", ["create", "decide", "execute"])
def test_v2_action_entrypoints_read_rollout_control_when_disabled(monkeypatch, entrypoint):
    """The final action boundary must not bypass durable control when the flag is off."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", False)
    gate = Mock(side_effect=DomainError("AGENT_RUN_V2_DISABLED", "Agent V2 已禁用", 409))
    monkeypatch.setattr(action_service, "require_v2_execution", gate)
    monkeypatch.setattr(action_service, "get_cursor", lambda: pytest.fail("disabled action must not write"))

    with pytest.raises(DomainError) as error:
        if entrypoint == "create":
            _create_proposal()
        elif entrypoint == "decide":
            action_service.decide_action_proposal(
                RUN_ID, PROPOSAL_ID, _approved(), "reviewer", "analyst"
            )
        else:
            action_service.execute_sourcing_risk_action({"payload": {}})

    assert error.value.code == "AGENT_RUN_V2_DISABLED"
    gate.assert_called_once_with(settings)


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
    monkeypatch.setattr(
        action_service,
        "get_action_candidate_for_update",
        lambda *_: {"id": "foreign-candidate", "run_id": OTHER_RUN_ID},
        raising=False,
    )

    with pytest.raises(DomainError) as exc:
        _create_proposal(
            payload={"company_name": "外部企业 A"},
            idempotency_key="import:external:foreign-candidate",
            candidate_id="foreign-candidate",
        )

    assert exc.value.code == "AGENT_ACTION_CANDIDATE_NOT_FOUND"


@pytest.mark.parametrize(
    ("candidate", "expected_code"),
    [
        (None, "AGENT_ACTION_CANDIDATE_REQUIRED"),
        ({"id": "candidate-a", "run_id": RUN_ID, "source": "local", "status": "staged_candidate", "candidate_snapshot": {}}, "AGENT_ACTION_CANDIDATE_INVALID"),
        ({"id": "candidate-a", "run_id": RUN_ID, "source": "external", "status": "reviewed", "candidate_snapshot": {}}, "AGENT_ACTION_CANDIDATE_INVALID"),
        ({"id": "candidate-a", "run_id": OTHER_RUN_ID, "source": "external", "status": "staged_candidate", "candidate_snapshot": {}}, "AGENT_ACTION_CANDIDATE_NOT_FOUND"),
    ],
)
def test_external_import_requires_current_run_staged_external_candidate(monkeypatch, candidate, expected_code):
    """Removing candidate source/state checks would allow unreviewed supplier-master imports."""
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(action_service, "get_run_for_update", lambda *_: _run())
    monkeypatch.setattr(action_service, "get_action_proposal_for_idempotency_key", lambda *_: None)
    monkeypatch.setattr(action_service, "get_action_candidate_for_update", lambda *_: candidate, raising=False)

    with pytest.raises(DomainError) as exc:
        _create_proposal(candidate_id="candidate-a" if candidate is not None else None)

    assert (exc.value.code, exc.value.status_code) == (expected_code, 404 if expected_code.endswith("NOT_FOUND") else 422)


def test_external_import_uses_persisted_staged_candidate_snapshot(monkeypatch):
    """Accepting caller payload would let an approved candidate import different master data."""
    persisted: list[dict] = []
    snapshot = {"company_name": "已暂存企业", "risk_level": "high", "unified_code": "91310000TEST00001"}
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(action_service, "get_run_for_update", lambda *_: _run())
    monkeypatch.setattr(action_service, "get_action_proposal_for_idempotency_key", lambda *_: None)
    monkeypatch.setattr(
        action_service,
        "get_action_candidate_for_update",
        lambda *_: {"id": "candidate-a", "run_id": RUN_ID, "source": "external", "status": "staged_candidate", "candidate_snapshot": snapshot},
        raising=False,
    )
    monkeypatch.setattr(action_service, "insert_action_proposal", lambda **kwargs: persisted.append(kwargs) or _proposal(payload=kwargs["payload"]))

    _create_proposal(payload={"company_name": "调用方伪造企业", "risk_level": "low"})

    assert persisted[0]["payload"] == snapshot


def test_external_import_accepts_graph_persisted_staged_external_candidate(monkeypatch):
    """Rejecting the graph's durable external source makes approval-gated import unreachable."""
    persisted: list[dict] = []
    snapshot = {"company_name": "图暂存外部企业", "risk_level": "low"}
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(action_service, "get_run_for_update", lambda *_: _run())
    monkeypatch.setattr(action_service, "get_action_proposal_for_idempotency_key", lambda *_: None)
    monkeypatch.setattr(
        action_service,
        "get_action_candidate_for_update",
        lambda *_: {
            "id": "candidate-a",
            "run_id": RUN_ID,
            "source": "staged_external",
            "status": "staged_candidate",
            "candidate_snapshot": snapshot,
        },
        raising=False,
    )
    monkeypatch.setattr(
        action_service,
        "insert_action_proposal",
        lambda **kwargs: persisted.append(kwargs) or _proposal(payload=kwargs["payload"]),
    )

    proposal = _create_proposal(payload={"company_name": "调用方伪造企业"})

    assert proposal["payload"] == snapshot
    assert persisted[0]["payload"] == snapshot


def test_proposal_creation_requires_authorized_creator_and_current_version(monkeypatch):
    """Skipping creator scope or optimistic locking would create approvable writes for stale or foreign Runs."""
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(action_service, "get_run_for_update", lambda *_: _run(user_id="owner"))

    with pytest.raises(DomainError) as forbidden:
        _create_proposal(user_id="foreign")
    assert (forbidden.value.code, forbidden.value.status_code) == ("AGENT_ACTION_PROPOSAL_FORBIDDEN", 403)

    with pytest.raises(DomainError) as stale:
        _create_proposal(expected_version=2)
    assert (stale.value.code, stale.value.status_code) == ("AGENT_RUN_VERSION_CONFLICT", 409)


def test_idempotency_key_rejects_changed_payload_for_the_same_action(monkeypatch):
    """Returning a proposal for mismatched request data would hide an idempotency-key collision."""
    existing = _proposal(candidate_id="candidate-a", payload={"company_name": "已暂存企业", "risk_level": "high"})
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(action_service, "get_run_for_update", lambda *_: _run())
    monkeypatch.setattr(action_service, "get_action_proposal_for_idempotency_key", lambda *_: existing)
    monkeypatch.setattr(
        action_service,
        "get_action_candidate_for_update",
        lambda *_: {"id": "candidate-a", "run_id": RUN_ID, "source": "external", "status": "staged_candidate", "candidate_snapshot": {"company_name": "其他企业", "risk_level": "high"}},
        raising=False,
    )

    with pytest.raises(DomainError) as exc:
        _create_proposal()

    assert (exc.value.code, exc.value.status_code) == ("AGENT_ACTION_IDEMPOTENCY_CONFLICT", 409)


def test_concurrent_idempotency_insert_rechecks_the_durable_proposal(monkeypatch):
    """A unique-key race must return the durable replay result instead of leaking a database error."""
    existing = _proposal(candidate_id="candidate-a", payload={"company_name": "外部企业 A", "risk_level": "low"})
    reads = iter((None, existing))
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(action_service, "get_run_for_update", lambda *_: _run())
    monkeypatch.setattr(action_service, "get_action_proposal_for_idempotency_key", lambda *_: next(reads))
    monkeypatch.setattr(action_service, "insert_action_proposal", lambda **_: None)
    monkeypatch.setattr(
        action_service,
        "get_action_candidate_for_update",
        lambda *_: {"id": "candidate-a", "run_id": RUN_ID, "source": "external", "status": "staged_candidate", "candidate_snapshot": {"company_name": "外部企业 A", "risk_level": "low"}},
        raising=False,
    )

    assert _create_proposal() == existing


@pytest.mark.parametrize("action_type", ("add_watchlist", "submit_access_application"))
def test_existing_company_actions_require_a_target_owned_by_the_current_run(monkeypatch, action_type):
    """Accepting a bare company name would create a proposal detached from the reviewed Run target."""
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(action_service, "get_run_for_update", lambda *_: _run())
    monkeypatch.setattr(action_service, "get_action_proposal_for_idempotency_key", lambda *_: None)
    monkeypatch.setattr(action_service, "insert_action_proposal", lambda **_: _proposal())

    with pytest.raises(DomainError) as exc:
        _create_proposal(
            action_type=action_type,
            payload={"company_name": "未绑定企业", "applicant_id": "requester"},
            idempotency_key=f"{action_type}:missing-target",
            candidate_id=None,
        )

    assert (exc.value.code, exc.value.status_code) == ("AGENT_ACTION_TARGET_REQUIRED", 422)


def test_existing_company_action_rejects_company_that_does_not_match_its_current_run_candidate(monkeypatch):
    """Permitting a candidate/company mix-up would approve a write against a different supplier target."""
    monkeypatch.setattr(action_service, "get_cursor", _cursor)
    monkeypatch.setattr(action_service, "get_run_for_update", lambda *_: _run())
    monkeypatch.setattr(action_service, "get_action_proposal_for_idempotency_key", lambda *_: None)
    monkeypatch.setattr(action_service, "insert_action_proposal", lambda **_: _proposal())
    monkeypatch.setattr(
        action_service,
        "get_action_candidate_for_update",
        lambda *_: {"id": "candidate-a", "run_id": RUN_ID, "company_id": "company-a"},
        raising=False,
    )

    with pytest.raises(DomainError) as exc:
        _create_proposal(
            action_type="add_watchlist",
            payload={"company_id": "company-b", "company_name": "企业 B"},
            idempotency_key="watchlist:candidate-company-mismatch",
            candidate_id="candidate-a",
        )

    assert (exc.value.code, exc.value.status_code) == ("AGENT_ACTION_COMPANY_INVALID", 422)


def _delete_real_action_run(run_id: str) -> None:
    with get_cursor() as (_, cur):
        cur.execute("DELETE FROM outbox_events WHERE aggregate_id = %s", (run_id,))
        cur.execute("DELETE FROM agent_runs WHERE id = %s", (run_id,))


def test_real_postgres_action_transaction_keeps_one_approval_outbox_and_conflict_contract():
    """Replacing proposal locking, transaction reuse, or conflict checks breaks durable approval state."""
    ensure_pg_schema()
    run = agent_run_repo.insert_run(
        "sourcing_risk_v2",
        {"requirement_text": "真实事务测试"},
        status="ACTION_PENDING",
    )
    try:
        candidate = agent_run_repo.insert_candidate(
            run["id"],
            "external",
            "staged_candidate",
            {"company_name": "真实暂存外部企业", "risk_level": "low"},
        )
        proposal = action_service.create_action_proposal(
            run["id"],
            "import_external_supplier",
            {"company_name": "调用方企业"},
            f"test-real-action-{run['id']}",
            candidate_id=candidate["id"],
            user_id="test-admin",
            user_role="admin",
            expected_version=1,
        )
        assert proposal["payload"] == {"company_name": "真实暂存外部企业", "risk_level": "low"}

        decided = action_service.decide_action_proposal(
            run["id"],
            proposal["id"],
            ApprovalDecisionRequest(expected_version=1, decision="approved"),
            None,  # type: ignore[arg-type]
            "admin",
        )
        assert decided["run"]["status"] == "ACTION_EXECUTING"

        with get_cursor() as (_, cur):
            cur.execute("SELECT COUNT(*) FROM agent_approval_decisions WHERE proposal_id = %s", (proposal["id"],))
            assert cur.fetchone() == (1,)
            cur.execute("SELECT COUNT(*) FROM outbox_events WHERE aggregate_id = %s", (proposal["id"],))
            assert cur.fetchone() == (1,)

        with pytest.raises(DomainError) as duplicate:
            action_service.decide_action_proposal(
                run["id"],
                proposal["id"],
                ApprovalDecisionRequest(expected_version=2, decision="approved"),
                None,  # type: ignore[arg-type]
                "admin",
            )
        assert (duplicate.value.code, duplicate.value.status_code) == ("AGENT_ACTION_ALREADY_DECIDED", 409)

        with get_cursor() as (_, cur):
            cur.execute("SELECT COUNT(*) FROM agent_approval_decisions WHERE proposal_id = %s", (proposal["id"],))
            assert cur.fetchone() == (1,)
            cur.execute("SELECT COUNT(*) FROM outbox_events WHERE aggregate_id = %s", (proposal["id"],))
            assert cur.fetchone() == (1,)
    finally:
        _delete_real_action_run(run["id"])


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
