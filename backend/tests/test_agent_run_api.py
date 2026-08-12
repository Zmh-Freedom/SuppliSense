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


def test_clarification_restarts_v2_runner_after_durable_answer_event(
    agent_client, agent_headers, monkeypatch: pytest.MonkeyPatch
):
    """Leaving clarification on the legacy event path would strand a clarified V2 run."""
    from app.domains.agent_run import api

    started: list[str] = []
    monkeypatch.setattr(
        api,
        "submit_clarification",
        lambda *_: {"id": RUN_ID, "status": "CREATED", "version": 2},
    )

    async def start(run_id: str) -> None:
        started.append(run_id)

    async def schedule(coroutine):
        await coroutine

    monkeypatch.setattr(api, "start_sourcing_risk_graph", start)
    monkeypatch.setattr(api, "_schedule_graph", schedule)

    response = agent_client.post(
        f"/api/v1/agent-runs/{RUN_ID}/clarification",
        headers=agent_headers,
        json={"expected_version": 1, "answers": {"specification": "IP67"}},
    )

    assert response.status_code == 200
    assert started == [RUN_ID]
