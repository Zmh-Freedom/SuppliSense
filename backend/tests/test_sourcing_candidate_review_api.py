from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.schemas.user import UserInDB


RUN_ID = "00000000-0000-4000-8000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000011"


@pytest.fixture
def sourcing_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from app.main import app

    @asynccontextmanager
    async def no_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def sourcing_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_RUN_V2_ROLLOUT", "default")
    user = UserInDB(
        id=USER_ID,
        username="sourcing-review-user",
        email="sourcing-review@example.com",
        role="purchaser",
        password_hash="unused",
        created_at=datetime.now(timezone.utc),
        is_active=True,
    )
    monkeypatch.setattr("app.core.deps.get_user_by_id", lambda _: user)
    return {"Authorization": f"Bearer {create_access_token({'sub': USER_ID})}"}


def test_candidate_decisions_get_uses_separate_response_contract(
    sourcing_client: TestClient,
    sourcing_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.agent_run import api

    monkeypatch.setattr(api, "_require_v2_route", lambda *_args, **_kwargs: "execute")
    monkeypatch.setattr(
        api,
        "get_candidate_decisions",
        lambda *_: {
            "run_id": RUN_ID,
            "run_version": 4,
            "dispositions": [{"candidate_key": "candidate-1", "action": "defer", "reason": "待补资料"}],
            "resume_status": "pending",
            "new_run_id": None,
        },
    )

    response = sourcing_client.get(
        f"/api/v1/sourcing-workspace/runs/{RUN_ID}/candidate-decisions",
        headers=sourcing_headers,
    )

    assert response.status_code == 200
    assert set(response.json()) == {"run_id", "run_version", "dispositions", "resume_status", "new_run_id"}
    assert response.json()["run_version"] == 4


def test_candidate_decisions_post_passes_idempotency_key_and_versioned_batch(
    sourcing_client: TestClient,
    sourcing_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.agent_run import api

    calls: list[tuple] = []
    monkeypatch.setattr(api, "_require_v2_route", lambda *_args, **_kwargs: "execute")
    monkeypatch.setattr(
        api,
        "submit_candidate_decisions",
        lambda *args: calls.append(args) or {
            "run_id": RUN_ID,
            "run_version": 5,
            "dispositions": [{"candidate_key": "candidate-1", "action": "confirm", "company_id": "company-1"}],
            "resume_status": "pending",
            "new_run_id": None,
        },
    )

    response = sourcing_client.post(
        f"/api/v1/sourcing-workspace/runs/{RUN_ID}/candidate-decisions",
        headers={**sourcing_headers, "Idempotency-Key": "candidate-review-1"},
        json={
            "expected_version": 4,
            "decisions": [{"candidate_key": "candidate-1", "action": "confirm", "company_id": "company-1"}],
        },
    )

    assert response.status_code == 200
    assert calls[0][0] == RUN_ID
    assert calls[0][1] == {
        "expected_version": 4,
        "decisions": [{"candidate_key": "candidate-1", "action": "confirm", "company_id": "company-1", "reason": None}],
    }
    assert calls[0][2:] == (USER_ID, "purchaser", "candidate-review-1")


def test_candidate_decisions_write_rejects_shadow_before_persisting(
    sourcing_client: TestClient,
    sourcing_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    from app.domains.agent_run import api

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ROLLOUT", "shadow")
    monkeypatch.setattr(api, "submit_candidate_decisions", lambda *_: pytest.fail("shadow must not write candidate dispositions"))

    response = sourcing_client.post(
        f"/api/v1/sourcing-workspace/runs/{RUN_ID}/candidate-decisions",
        headers={**sourcing_headers, "Idempotency-Key": "candidate-review-shadow"},
        json={"expected_version": 4, "decisions": []},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_RUN_V2_SHADOW_READ_ONLY"
