"""HTTP and SSE contracts for the durable agent-run resource."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.errors import DomainError
from app.core.security import create_access_token
from app.schemas.user import UserInDB


RUN_ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def agent_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Run router contracts without requiring local database services at lifespan startup."""
    from app.main import app

    @asynccontextmanager
    async def no_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def agent_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_RUN_V2_ROLLOUT", "default")
    user = UserInDB(
        id="00000000-0000-0000-0000-000000000011",
        username="agent-run-user",
        email="agent-run@example.com",
        role="analyst",
        password_hash="unused",
        created_at=datetime.now(timezone.utc),
        is_active=True,
    )
    monkeypatch.setattr("app.core.deps.get_user_by_id", lambda _: user)
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


def test_agent_run_endpoints_require_authentication(agent_client):
    """Removing the router auth dependency would expose requirements and event history."""
    response = agent_client.get(f"/api/v1/agent-runs/{RUN_ID}")

    assert response.status_code == 401


def test_event_endpoint_replays_events_after_last_event_id(agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch):
    """Ignoring Last-Event-ID would replay an already applied event after reconnect."""
    from app.domains.agent_run import api

    observed: list[int] = []

    def fake_stream(run_id: str, last_event_id: int, user_id: str, user_role: str):
        observed.extend([last_event_id])
        return iter([{"event_id": 4, "event_type": "stage", "data": {"status": "SCORING"}}])

    monkeypatch.setattr(api, "get_sourcing_risk_run", lambda *_: {"id": RUN_ID})
    monkeypatch.setattr(api, "stream_events", fake_stream)

    response = agent_client.get(
        f"/api/v1/agent-runs/{RUN_ID}/events",
        headers={**agent_headers, "Last-Event-ID": "3"},
    )

    assert response.status_code == 200
    assert observed == [3]
    assert "id: 4" in response.text
    assert "event: stage" in response.text
    assert 'data: {"status": "SCORING"}' in response.text


def test_event_endpoint_cors_preflight_allows_last_event_id(agent_client):
    """Omitting this header blocks browser SSE resume before the event route is reached."""
    response = agent_client.options(
        f"/api/v1/agent-runs/{RUN_ID}/events",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Last-Event-ID",
        },
    )

    assert response.status_code == 200
    assert "last-event-id" in response.headers["access-control-allow-headers"].lower()


def test_event_endpoint_rejects_foreign_or_missing_run_before_opening_stream(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch
):
    """Lazy authorization turns a missing Run into a 200 response with an in-stream failure."""
    from app.domains.agent_run import api

    monkeypatch.setattr(
        api,
        "get_sourcing_risk_run",
        lambda *_: (_ for _ in ()).throw(DomainError("AGENT_RUN_NOT_FOUND", "任务不存在", 404)),
    )

    response = agent_client.get(f"/api/v1/agent-runs/{RUN_ID}/events", headers=agent_headers)

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "AGENT_RUN_NOT_FOUND", "message": "任务不存在", "detail": None}
    }


def test_detail_maps_foreign_run_to_not_found(agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch):
    """Leaking a 403 here would confirm that another user's Run exists."""
    from app.domains.agent_run import api

    monkeypatch.setattr(
        api,
        "get_sourcing_risk_run",
        lambda *_: (_ for _ in ()).throw(DomainError("AGENT_RUN_NOT_FOUND", "任务不存在", 404)),
    )

    response = agent_client.get(f"/api/v1/agent-runs/{RUN_ID}", headers=agent_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_RUN_NOT_FOUND"


def test_detail_returns_workbench_collections(agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch):
    """The HTTP detail contract must expose persisted V2 collections, not only run metadata."""
    from app.domains.agent_run import api

    monkeypatch.setattr(api, "get_sourcing_risk_run", lambda *_: {
        "id": RUN_ID,
        "status": "ACTION_PENDING",
        "version": 4,
        "requirement": {"requirement_text": "采购工业摄像头"},
        "candidates": [{"id": "candidate-1", "supplier_name": "示例供应商"}],
        "evidence_by_company_id": {"company-1": [{"evidence_id": "evidence-1"}]},
        "evidence_reviews": {"company-1": {"status": "clear"}},
        "decisions": [{"candidate_id": "candidate-1", "group": "recommended"}],
        "action_proposals": [{"id": "approval-1", "action_type": "add_watchlist", "status": "pending", "payload": {}}],
        "approvals": [{"proposal_id": "approval-1", "decision": "approved", "comment": "复核通过"}],
    })

    response = agent_client.get(f"/api/v1/agent-runs/{RUN_ID}", headers=agent_headers)

    assert response.status_code == 200
    assert response.json()["run_id"] == RUN_ID
    assert response.json()["id"] == RUN_ID
    assert response.json()["candidates"][0]["id"] == "candidate-1"
    assert response.json()["evidence_by_company_id"]["company-1"][0]["evidence_id"] == "evidence-1"
    assert response.json()["decisions"][0]["group"] == "recommended"
    assert response.json()["action_proposals"][0]["id"] == "approval-1"
    assert response.json()["approvals"][0]["comment"] == "复核通过"


def test_raw_payload_compensation_retry_uses_authorized_run_endpoint(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch
):
    """Recovery must be scoped by the server-authorized Run, never by caller-supplied raw references."""
    from app.domains.agent_run import api

    observed: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        api,
        "retry_sourcing_risk_raw_payload_compensations",
        lambda run_id, user_id, role: observed.append((run_id, user_id, role)) or [
            {"raw_payload_ref": "raw-1", "lifecycle_status": "compensated"}
        ],
    )

    response = agent_client.post(
        f"/api/v1/agent-runs/{RUN_ID}/raw-payload-compensations/retry",
        headers=agent_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"raw_payload_statuses": [{"raw_payload_ref": "raw-1", "lifecycle_status": "compensated"}]}
    assert observed[0][0] == RUN_ID


@pytest.mark.parametrize("lifecycle_status", ["pending_compensation", "unknown"])
def test_raw_payload_compensation_retry_preserves_unsafe_status(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch, lifecycle_status: str
):
    """The retry API must not turn an unsafe Mongo result into compensated."""
    from app.domains.agent_run import api

    monkeypatch.setattr(
        api,
        "retry_sourcing_risk_raw_payload_compensations",
        lambda *_: [{"raw_payload_ref": "raw-1", "lifecycle_status": lifecycle_status}],
    )

    response = agent_client.post(
        f"/api/v1/agent-runs/{RUN_ID}/raw-payload-compensations/retry",
        headers=agent_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "raw_payload_statuses": [
            {"raw_payload_ref": "raw-1", "lifecycle_status": lifecycle_status}
        ]
    }


def test_approval_decision_uses_canonical_nested_path(agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch):
    """The documented nested decisions path keeps approval intent unambiguous while old clients retain their route."""
    from app.domains.agent_run import api

    observed: list[object] = []
    monkeypatch.setattr(api, "decide_action_proposal", lambda *args: observed.append(args) or {"run": {"id": RUN_ID}})

    response = agent_client.post(
        f"/api/v1/agent-runs/{RUN_ID}/approvals/{RUN_ID}/decisions",
        headers=agent_headers,
        json={"expected_version": 4, "decision": "rejected", "comment": "缺少合规材料"},
    )

    assert response.status_code == 200
    assert observed[0][2].expected_version == 4
    assert observed[0][2].decision == "rejected"
    assert observed[0][2].comment == "缺少合规材料"


def test_cancel_propagates_stale_version_conflict(agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch):
    """Swallowing a service conflict would make clients believe a stale cancellation succeeded."""
    from app.domains.agent_run import api

    monkeypatch.setattr(
        api,
        "cancel_run",
        lambda *_: (_ for _ in ()).throw(
            DomainError("AGENT_RUN_VERSION_CONFLICT", "任务版本已变更", 409)
        ),
    )

    response = agent_client.post(
        f"/api/v1/agent-runs/{RUN_ID}/cancel",
        headers=agent_headers,
        json={"expected_version": 2},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_RUN_VERSION_CONFLICT"


def test_create_agent_run_starts_v2_runner_after_persisting_run(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch
):
    """Removing the API-to-runner seam would leave durable runs permanently CREATED."""
    from app.domains.agent_run import api

    started: list[str] = []
    monkeypatch.setattr(api, "create_sourcing_risk_run", lambda *_: {"id": RUN_ID, "status": "CREATED"})

    async def start(run_id: str) -> None:
        started.append(run_id)

    monkeypatch.setattr(api, "start_sourcing_risk_graph", start)
    async def schedule(coroutine):
        await coroutine

    monkeypatch.setattr(api, "_schedule_graph", schedule)

    response = agent_client.post(
        "/api/v1/agent-runs",
        headers=agent_headers,
        json={"requirement_text": "采购工业摄像头"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == RUN_ID
    assert started == [RUN_ID]


def test_create_agent_run_rejects_when_v2_route_is_disabled(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch
):
    from app.domains.agent_run import api
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", False)
    monkeypatch.setattr(api, "create_sourcing_risk_run", lambda *_: pytest.fail("must not create V2 run"))

    response = agent_client.post(
        "/api/v1/agent-runs", headers=agent_headers, json={"requirement_text": "采购工业摄像头"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_RUN_V2_DISABLED"


def test_shadow_route_blocks_approval_before_domain_write(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch
):
    from app.domains.agent_run import api
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_RUN_V2_ROLLOUT", "shadow")
    monkeypatch.setattr(api, "decide_action_proposal", lambda *_: pytest.fail("shadow must not write"))

    response = agent_client.post(
        f"/api/v1/agent-runs/{RUN_ID}/approvals/{RUN_ID}/decisions",
        headers=agent_headers,
        json={"expected_version": 4, "decision": "approved"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_RUN_V2_SHADOW_READ_ONLY"


@pytest.mark.parametrize(
    ("rollout", "role", "canary_percent", "expected_code"),
    [
        ("internal", "viewer", 0, "AGENT_RUN_V2_NOT_IN_ROLLOUT"),
        ("canary", "analyst", 0, "AGENT_RUN_V2_NOT_IN_ROLLOUT"),
    ],
)
def test_agent_run_create_route_matrix_rejects_non_v2_users(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch, rollout, role, canary_percent, expected_code
):
    from app.core.config import settings
    from app.domains.agent_run import api

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_RUN_V2_ROLLOUT", rollout)
    monkeypatch.setattr(settings, "AGENT_RUN_V2_CANARY_PERCENT", canary_percent)
    monkeypatch.setattr(api, "create_sourcing_risk_run", lambda *_: pytest.fail("must not create non-V2 run"))
    monkeypatch.setattr(api, "get_current_user", lambda: None)
    monkeypatch.setattr("app.core.deps.get_user_by_id", lambda _: UserInDB(
        id="00000000-0000-0000-0000-000000000011", username="u", email="u@example.com",
        role=role, password_hash="unused", created_at=datetime.now(timezone.utc), is_active=True,
    ))
    response = agent_client.post("/api/v1/agent-runs", headers=agent_headers, json={"requirement_text": "采购工业摄像头"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == expected_code


def test_shadow_create_rejects_before_persisting_or_starting_graph_legacy_slot(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch
):
    from app.core.config import settings
    from app.domains.agent_run import api

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_RUN_V2_ROLLOUT", "shadow")
    monkeypatch.setattr(api, "create_sourcing_risk_run", lambda *_: pytest.fail("shadow must not persist a V2 run"))
    monkeypatch.setattr(api, "start_sourcing_risk_graph", lambda *_: pytest.fail("shadow must not start a V2 graph"))
    response = agent_client.post("/api/v1/agent-runs", headers=agent_headers, json={"requirement_text": "采购工业摄像头"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_RUN_V2_SHADOW_READ_ONLY"


def test_shadow_create_rejects_before_persisting_or_starting_graph(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch
):
    from app.core.config import settings
    from app.domains.agent_run import api

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_RUN_V2_ROLLOUT", "shadow")
    monkeypatch.setattr(api, "create_sourcing_risk_run", lambda *_: pytest.fail("shadow must not persist a V2 run"))
    monkeypatch.setattr(api, "start_sourcing_risk_graph", lambda *_: pytest.fail("shadow must not start a V2 graph"))

    response = agent_client.post(
        "/api/v1/agent-runs", headers=agent_headers, json={"requirement_text": "采购工业摄像头"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_RUN_V2_SHADOW_READ_ONLY"


@pytest.mark.parametrize("endpoint", ["clarification", "identity-resolution"])
def test_shadow_resume_rejects_before_domain_write_or_graph_resume(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch, endpoint: str
):
    from app.core.config import settings
    from app.domains.agent_run import api

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_RUN_V2_ROLLOUT", "shadow")
    monkeypatch.setattr(api, "submit_clarification", lambda *_: pytest.fail("shadow must not write clarification"))
    monkeypatch.setattr(api, "submit_identity_resolution", lambda *_: pytest.fail("shadow must not write identity"))
    monkeypatch.setattr(api, "resume_sourcing_risk_graph", lambda *_: pytest.fail("shadow must not resume graph"))

    payload = {"expected_version": 1, "answers": {"specification": "IP67"}}
    if endpoint == "identity-resolution":
        payload = {"expected_version": 1, "resolutions": {"candidate-a": "company-a"}}
    response = agent_client.post(
        f"/api/v1/agent-runs/{RUN_ID}/{endpoint}", headers=agent_headers, json=payload
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_RUN_V2_SHADOW_READ_ONLY"


def test_identity_resolution_persists_input_then_resumes_v2_runner(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch
):
    """Dropping a reviewer's resolution before resume would re-interrupt the same checkpoint."""
    from app.domains.agent_run import api

    resumed: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        api,
        "submit_identity_resolution",
        lambda *_: {"id": RUN_ID, "status": "IDENTITY_REVIEW", "version": 3},
    )

    async def resume(run_id: str, payload: dict[str, object]) -> None:
        resumed.append((run_id, payload))

    monkeypatch.setattr(api, "resume_sourcing_risk_graph", resume)
    async def schedule(coroutine):
        await coroutine

    monkeypatch.setattr(api, "_schedule_graph", schedule)

    response = agent_client.post(
        f"/api/v1/agent-runs/{RUN_ID}/identity-resolution",
        headers=agent_headers,
        json={"expected_version": 2, "resolutions": {"candidate-a": "company-a"}},
    )

    assert response.status_code == 200
    assert response.json()["version"] == 3
    assert resumed == [(RUN_ID, {"identity_resolutions": {"candidate-a": "company-a"}})]


def test_clarification_resumes_v2_runner_with_durable_requirement_patch(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch
):
    """Restarting a clarified V2 run would abandon its durable checkpoint interrupt."""
    from app.domains.agent_run import api

    resumed: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        api,
        "submit_clarification",
        lambda *_: {"id": RUN_ID, "status": "CREATED", "version": 2, "requirement": {"requirement_text": "采购工业摄像头", "specification": "IP67"}},
    )

    async def resume(run_id: str, payload: dict[str, object]) -> None:
        resumed.append((run_id, payload))

    async def schedule(coroutine):
        await coroutine

    monkeypatch.setattr(api, "resume_sourcing_risk_graph", resume)
    monkeypatch.setattr(api, "_schedule_graph", schedule)

    response = agent_client.post(
        f"/api/v1/agent-runs/{RUN_ID}/clarification",
        headers=agent_headers,
        json={"expected_version": 1, "answers": {"specification": "IP67"}},
    )

    assert response.status_code == 200
    assert resumed == [(RUN_ID, {"requirement_input": {"requirement_text": "采购工业摄像头", "specification": "IP67"}})]


def test_clarification_api_service_runner_seam_consumes_durable_requirement_patch(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch
):
    """Restarting instead of resuming would strand a clarification checkpoint or ignore its answers."""
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from app.domains.agent_run import api, service
    from app.graphs.sourcing_risk_v2 import nodes, runner
    from app.graphs.sourcing_risk_v2.checkpointer import settings
    from langgraph.checkpoint.memory import InMemorySaver

    durable_run = {
        "id": RUN_ID,
        "user_id": "00000000-0000-0000-0000-000000000011",
        "status": "CLARIFYING",
        "version": 2,
        "requirement": {"requirement_text": "采购工业摄像头"},
    }
    persisted_events: list[dict] = []
    candidate = {
        "supplier_name": "示例供应商",
        "company_id": "company-a",
        "identity_company_id": "company-a",
        "active": True,
        "categories": ["摄像头"],
        "specifications": ["IP67"],
    }
    saver = InMemorySaver()
    invocations: list[tuple[object, dict]] = []
    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", True)

    class RecordingGraph:
        async def ainvoke(self, input_value, config):
            invocations.append((input_value, config))
            return await graph.ainvoke(input_value, config)

    monkeypatch.setattr(service, "get_cursor", _no_cursor)
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: dict(durable_run))

    def update_requirement(_run_id, expected_version, requirement, status, **_kwargs):
        durable_run.update({"requirement": requirement, "status": status, "version": expected_version + 1})
        return dict(durable_run)

    monkeypatch.setattr(service, "update_run_requirement", update_requirement)
    monkeypatch.setattr(
        service,
        "append_event",
        lambda run_id, version, event_type, payload, **_: persisted_events.append(
            {"run_id": run_id, "version": version, "event_type": event_type, "payload": payload}
        ),
    )
    monkeypatch.setattr(nodes, "record_orchestration_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(nodes, "persist_orchestration_snapshot", lambda *_args, **_kwargs: {"candidates": []})
    monkeypatch.setattr(nodes, "parse_requirement", lambda raw_text, data: {
        "status": "ready", "requirement": {"category": "摄像头", "specification": data["specification"]}
    } if data.get("specification") else {"status": "clarification_required", "missing": ["specification"]})
    monkeypatch.setattr(nodes, "freeze_policy_snapshot", lambda *_: {"checksum": "policy-1"})
    monkeypatch.setattr(nodes, "discover_local_candidates", lambda *_: [candidate])
    monkeypatch.setattr(nodes, "is_candidate_supply_sufficient", lambda *_: True)
    monkeypatch.setattr(nodes, "resolve_candidate_identity", lambda *_: {"identity_status": "exact", "company_id": "company-a"})
    monkeypatch.setattr(nodes, "investigate_candidates", AsyncMock(return_value=({"company-a": []}, [])))
    monkeypatch.setattr(nodes, "validate_evidence_set", lambda *_: {"status": "clear", "reason_codes": []})
    monkeypatch.setattr(nodes, "decide_candidates", lambda *_: [])
    graph = runner.build_sourcing_risk_graph(saver)
    config = {"configurable": {"thread_id": RUN_ID}}
    paused = asyncio.run(graph.ainvoke({"run_id": RUN_ID, "requirement_input": dict(durable_run["requirement"])}, config))
    assert paused["__interrupt__"]

    started: list[str] = []

    async def start(run_id: str) -> None:
        started.append(run_id)

    monkeypatch.setattr(runner, "get_sourcing_risk_checkpointer", AsyncMock(return_value=saver))
    monkeypatch.setattr(runner, "build_sourcing_risk_graph", Mock(return_value=RecordingGraph()))
    monkeypatch.setattr(api, "start_sourcing_risk_graph", start)
    monkeypatch.setattr(api, "resume_sourcing_risk_graph", runner._resume)

    async def schedule(coroutine):
        await coroutine

    monkeypatch.setattr(api, "_schedule_graph", schedule)

    response = agent_client.post(
        f"/api/v1/agent-runs/{RUN_ID}/clarification",
        headers=agent_headers,
        json={"expected_version": 2, "answers": {"specification": "IP67"}},
    )

    assert response.status_code == 200
    assert durable_run["status"] == "CREATED"
    assert persisted_events == [{
        "run_id": RUN_ID,
        "version": 3,
        "event_type": "clarification",
        "payload": {"answers": {"specification": "IP67"}, "status": "CREATED"},
    }]
    assert started == []
    command, config = invocations[-1]
    assert command.resume == {"requirement_input": {"requirement_text": "采购工业摄像头", "specification": "IP67"}}
    assert config == {"configurable": {"thread_id": RUN_ID}}
    assert graph.get_state(config).values["requirement"]["specification"] == "IP67"


def _no_cursor():
    class CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, *_):
            return False

    return CursorContext()
