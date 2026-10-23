"""Authorized server-side handoff from a sourcing candidate into Chat."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


USER_ID = "00000000-0000-0000-0000-000000000011"
RUN_ID = "00000000-0000-4000-8000-000000000001"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000002"
COMPANY_A = "00000000-0000-4000-8000-000000000003"
COMPANY_B = "00000000-0000-4000-8000-000000000004"


@pytest.fixture
def sourcing_handoff_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from app.core.security import create_access_token
    from app.main import app
    from app.schemas.user import UserInDB

    @asynccontextmanager
    async def no_lifespan(_app):
        yield

    user = UserInDB(
        id=USER_ID,
        username="sourcing-handoff-user",
        email="sourcing-handoff@example.com",
        role="purchaser",
        password_hash="unused",
        created_at=datetime.now(timezone.utc),
        is_active=True,
    )
    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)
    monkeypatch.setattr("app.core.deps.get_user_by_id", lambda _user_id: user)
    from app.domains.sourcing_risk import handoff_repo

    monkeypatch.setattr("app.domains.agent_run.api._require_v2_route", lambda *_args, **_kwargs: "active")
    monkeypatch.setattr(handoff_repo, "read_handoff_source", lambda *_args: _source())
    monkeypatch.setattr(handoff_repo, "get_handoff_for_request", lambda *_args: None)
    monkeypatch.setattr(
        handoff_repo,
        "create_handoff_transaction",
        lambda *_args: {
            "handoff_id": "00000000-0000-4000-8000-000000000007",
            "session_id": "00000000-0000-4000-8000-000000000006",
        },
    )
    with TestClient(app) as client:
        client.headers.update({"Authorization": f"Bearer {create_access_token({'sub': USER_ID})}"})
        yield client


def test_create_candidate_handoff_returns_a_bound_chat_session(
    sourcing_handoff_client: TestClient,
) -> None:
    response = sourcing_handoff_client.post(
        "/api/v1/sourcing-workspace/handoffs",
        headers={"Idempotency-Key": "handoff-request-1"},
        json={
            "run_id": RUN_ID,
            "candidate_key": CANDIDATE_ID,
            "expected_version": 7,
            "intent": "risk_review",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["handoff_id"]
    assert payload["session_id"]
    assert payload["chat_url"] == f"/chat?handoff_id={payload['handoff_id']}&session_id={payload['session_id']}"


def _payload(**overrides):
    return {
        "run_id": RUN_ID,
        "candidate_key": CANDIDATE_ID,
        "expected_version": 7,
        "intent": "risk_review",
        **overrides,
    }


def _source(**overrides):
    source = {
        "run": {
            "id": RUN_ID,
            "run_type": "sourcing_risk_v2",
            "user_id": USER_ID,
            "version": 7,
            "status": "COMPLETED",
            "requirement": {"requirement_text": "采购工业相机"},
        },
        "candidate": {
            "candidate_key": CANDIDATE_ID,
            "supplier_name": "候选工厂",
            "source": "staged_external",
            "status": "identity_review",
            "identity_status": "ambiguous",
            "company_id": None,
            "identity_candidates": [
                {"company_id": COMPANY_A, "supplier_name": "甲工厂有限公司"},
                {"company_id": COMPANY_B, "supplier_name": "乙工厂有限公司"},
            ],
        },
        "disposition": None,
        "evidence_refs": [{
            "evidence_id": "00000000-0000-4000-8000-000000000005",
            "dimension": "quality",
            "source_type": "supplier_registry",
            "source_reference": "evidence://quality/old",
            "freshness_status": "stale",
            "collected_at": "2025-01-01T00:00:00+00:00",
        }],
    }
    source.update(overrides)
    return source


def _install_service_source(monkeypatch: pytest.MonkeyPatch, source: dict | None = None):
    from app.domains.sourcing_risk import handoff_service

    value = _source() if source is None else source
    monkeypatch.setattr(
        handoff_service.handoff_repo,
        "read_handoff_source",
        lambda *_args: value,
    )
    monkeypatch.setattr(handoff_service.handoff_repo, "get_handoff_for_request", lambda *_args: None)
    return handoff_service


def test_create_candidate_handoff_rejects_another_users_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import DomainError

    source = _source(run={**_source()["run"], "user_id": "another-user"})
    service = _install_service_source(monkeypatch, source)
    monkeypatch.setattr(service.handoff_repo, "create_handoff_transaction", lambda *_args: pytest.fail("must not create a session"))

    with pytest.raises(DomainError) as error:
        service.create_candidate_handoff(_payload(), USER_ID, "purchaser", "handoff-1")

    assert error.value.status_code == 404


def test_create_candidate_handoff_rejects_non_sourcing_parent_and_missing_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.errors import DomainError

    service = _install_service_source(monkeypatch, _source(run={**_source()["run"], "run_type": "agent_harness"}))
    monkeypatch.setattr(service.handoff_repo, "create_handoff_transaction", lambda *_args: pytest.fail("must not create a session"))
    with pytest.raises(DomainError) as wrong_run:
        service.create_candidate_handoff(_payload(), USER_ID, "purchaser", "handoff-1")
    assert wrong_run.value.status_code == 404

    service = _install_service_source(monkeypatch, _source(candidate=None))
    monkeypatch.setattr(service.handoff_repo, "create_handoff_transaction", lambda *_args: pytest.fail("must not create a session"))
    with pytest.raises(DomainError) as missing_candidate:
        service.create_candidate_handoff(_payload(), USER_ID, "purchaser", "handoff-2")
    assert missing_candidate.value.status_code == 404


def test_create_candidate_handoff_rejects_a_changed_parent_version(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import DomainError

    source = _source(run={**_source()["run"], "version": 8})
    service = _install_service_source(monkeypatch, source)
    monkeypatch.setattr(service.handoff_repo, "create_handoff_transaction", lambda *_args: pytest.fail("stale handoff must not create a session"))

    with pytest.raises(DomainError) as error:
        service.create_candidate_handoff(_payload(expected_version=7), USER_ID, "purchaser", "handoff-1")

    assert error.value.status_code == 409


def test_handoff_refresh_reuses_the_same_session_for_the_same_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _install_service_source(monkeypatch)
    stored: dict[str, dict] = {}
    created_sessions: list[str] = []

    def create_transaction(_source, payload, _user_id, _role, request_key, _request_hash):
        if request_key not in stored:
            session_id = "00000000-0000-4000-8000-000000000006"
            created_sessions.append(session_id)
            stored[request_key] = {
                "handoff_id": "00000000-0000-4000-8000-000000000007",
                "session_id": session_id,
                "chat_url": f"/chat?handoff_id=00000000-0000-4000-8000-000000000007&session_id={session_id}",
            }
        return stored[request_key]

    monkeypatch.setattr(service.handoff_repo, "create_handoff_transaction", create_transaction)

    first = service.create_candidate_handoff(_payload(), USER_ID, "purchaser", "handoff-refresh-1")
    second = service.create_candidate_handoff(_payload(), USER_ID, "purchaser", "handoff-refresh-1")

    assert first == second
    assert len(created_sessions) == 1


def test_get_candidate_handoff_does_not_disclose_another_users_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _install_service_source(monkeypatch)
    monkeypatch.setattr(service.handoff_repo, "get_handoff_for_user", lambda *_args: None)

    assert service.get_candidate_handoff("00000000-0000-4000-8000-000000000007", "another-user") is None


def test_resolve_rejects_a_modified_session_or_handoff_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import DomainError

    service = _install_service_source(monkeypatch)
    binding = {
        "handoff_id": "00000000-0000-4000-8000-000000000007",
        "session_id": "00000000-0000-4000-8000-000000000006",
        "user_id": USER_ID,
        "status": "ready",
        "source_run_id": RUN_ID,
        "source_run_version": 7,
        "candidate_key": CANDIDATE_ID,
        "intent": "risk_review",
        "continue_message": "请核验候选工厂的主体并继续风险检查",
    }
    monkeypatch.setattr(service.handoff_repo, "get_handoff_by_session", lambda session_id: binding if session_id == binding["session_id"] else None)
    context = {"_requested_handoff_id": "00000000-0000-4000-8000-000000000099", "_handoff_request_message": binding["continue_message"]}

    with pytest.raises(DomainError) as changed_session:
        service.resolve_session_handoff("00000000-0000-4000-8000-000000000008", USER_ID, context)
    assert changed_session.value.status_code == 404

    with pytest.raises(DomainError) as changed_handoff:
        service.resolve_session_handoff(binding["session_id"], USER_ID, context)
    assert changed_handoff.value.status_code == 404


def test_resolve_keeps_stale_evidence_historical_and_does_not_auto_select_ambiguous_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _install_service_source(monkeypatch)
    binding = {
        "handoff_id": "00000000-0000-4000-8000-000000000007",
        "session_id": "00000000-0000-4000-8000-000000000006",
        "user_id": USER_ID,
        "status": "ready",
        "source_run_id": RUN_ID,
        "source_run_version": 7,
        "candidate_key": CANDIDATE_ID,
        "intent": "risk_review",
        "continue_message": "请核验候选工厂的主体并继续风险检查",
        **_source(),
    }
    monkeypatch.setattr(service.handoff_repo, "get_handoff_by_session", lambda _session_id: binding)
    context = {
        "_requested_handoff_id": binding["handoff_id"],
        "_handoff_request_message": binding["continue_message"],
        "agent_user_id": USER_ID,
        "agent_user_role": "purchaser",
        "references": [],
        "conversation_state": {},
        "current_task": {},
    }

    resolved = service.resolve_session_handoff(binding["session_id"], USER_ID, context)

    handoff = resolved["sourcing_handoff"]
    assert handoff["requires_identity_confirmation"] is True
    assert handoff["selected_company_id"] is None
    assert handoff["evidence_refs"][0]["use"] == "historical"
    assert all(item.get("source_reference") != "evidence://quality/old" for item in resolved["references"])


def test_resolve_rechecks_freshness_against_frozen_source_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _install_service_source(monkeypatch)
    observed_at = datetime.now(timezone.utc) - timedelta(days=120)
    source = _source(
        run={**_source()["run"], "policy": {"freshness_days": {"quality": 90}}},
        evidence_refs=[{
            "evidence_id": "00000000-0000-4000-8000-000000000005",
            "dimension": "quality",
            "source_type": "supplier_registry",
            "source_reference": "evidence://quality/old-but-labeled-fresh",
            "freshness_status": "fresh",
            "observed_at": observed_at.isoformat(),
            "collected_at": observed_at.isoformat(),
        }],
    )
    monkeypatch.setattr(service.handoff_repo, "read_handoff_source", lambda *_args: source)
    binding = {
        "handoff_id": "00000000-0000-4000-8000-000000000007",
        "session_id": "00000000-0000-4000-8000-000000000006",
        "user_id": USER_ID,
        "status": "ready",
        "source_run_id": RUN_ID,
        "source_run_version": 7,
        "candidate_key": CANDIDATE_ID,
        "intent": "risk_review",
        "continue_message": "请核验候选工厂的主体并继续风险检查",
        **source,
    }
    monkeypatch.setattr(service.handoff_repo, "get_handoff_by_session", lambda _session_id: binding)

    resolved = service.resolve_session_handoff(binding["session_id"], USER_ID, {
        "_requested_handoff_id": binding["handoff_id"],
        "_handoff_request_message": binding["continue_message"],
        "agent_user_id": USER_ID,
        "agent_user_role": "purchaser",
        "references": [],
        "conversation_state": {},
        "current_task": {},
    })

    assert resolved["sourcing_handoff"]["evidence_refs"][0]["freshness_status"] == "stale"
    assert resolved["sourcing_handoff"]["evidence_refs"][0]["use"] == "historical"


def test_confirmed_disposition_uses_the_selected_companys_canonical_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _install_service_source(monkeypatch)
    source = _source(disposition={"action": "confirm", "company_id": COMPANY_B})
    monkeypatch.setattr(service.handoff_repo, "read_handoff_source", lambda *_args: source)
    monkeypatch.setattr(service, "can_access_handoff_candidate", lambda *_args: True)
    binding = {
        "handoff_id": "00000000-0000-4000-8000-000000000007",
        "session_id": "00000000-0000-4000-8000-000000000006",
        "user_id": USER_ID,
        "status": "ready",
        "source_run_id": RUN_ID,
        "source_run_version": 7,
        "candidate_key": CANDIDATE_ID,
        "intent": "risk_review",
        "continue_message": "请核验候选工厂并继续风险检查",
        **source,
    }
    monkeypatch.setattr(service.handoff_repo, "get_handoff_by_session", lambda _session_id: binding)

    resolved = service.resolve_session_handoff("00000000-0000-4000-8000-000000000006", USER_ID, {
        "_requested_handoff_id": binding["handoff_id"],
        "_handoff_request_message": binding["continue_message"],
        "agent_user_id": USER_ID,
        "agent_user_role": "purchaser",
        "references": [],
        "conversation_state": {},
        "current_task": {},
    })

    assert resolved["sourcing_handoff"]["selected_company_id"] == COMPANY_B
    assert resolved["sourcing_handoff"]["selected_supplier_name"] == "乙工厂有限公司"
    assert resolved["references"][0]["name"] == "乙工厂有限公司"


def test_resolve_rechecks_candidate_access_before_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import DomainError

    service = _install_service_source(monkeypatch)
    exact_source = _source(candidate={
        "candidate_key": CANDIDATE_ID,
        "supplier_name": "甲工厂有限公司",
        "source": "local",
        "status": "resolved",
        "identity_status": "exact",
        "company_id": COMPANY_A,
        "identity_candidates": [],
    })
    binding = {
        "handoff_id": "00000000-0000-4000-8000-000000000007",
        "session_id": "00000000-0000-4000-8000-000000000006",
        "user_id": USER_ID,
        "status": "ready",
        "source_run_id": RUN_ID,
        "source_run_version": 7,
        "candidate_key": CANDIDATE_ID,
        "intent": "risk_review",
        "continue_message": "分析甲工厂有限公司的风险",
        **exact_source,
    }
    monkeypatch.setattr(service.handoff_repo, "get_handoff_by_session", lambda _session_id: binding)
    monkeypatch.setattr(service.handoff_repo, "read_handoff_source", lambda *_args: exact_source)
    monkeypatch.setattr(service, "can_access_handoff_candidate", lambda *_args: False)

    with pytest.raises(DomainError) as error:
        service.resolve_session_handoff(binding["session_id"], USER_ID, {
            "_requested_handoff_id": binding["handoff_id"],
            "_handoff_request_message": binding["continue_message"],
            "agent_user_id": USER_ID,
            "agent_user_role": "purchaser",
        })

    assert error.value.status_code == 403
