"""Real PostgreSQL/Mongo/Redis control-plane checks for Agent 60 recovery."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import httpx
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.domains.agent_run.chat_interrupt_repo import (
    ack_chat_interrupt,
    claim_chat_interrupt,
    release_chat_interrupt,
)
from app.domains.agent_run.repo import get_run, list_events_after
from app.domains.agent_run.service import cancel_active_harness_run
from tests.evals.agent_60_fixture import (
    seed_agent_60_live_fixture,
    teardown_agent_60_live_fixture,
)
from tests.evals.agent_60_live_runner import run_agent_60_http_cases


pytestmark = [pytest.mark.integration, pytest.mark.agent_e2e, pytest.mark.agent_e2e_live]


@pytest.fixture
def agent_60_live_fixture():
    if not os.getenv("RUN_AGENT_60_LIVE"):
        pytest.skip("set RUN_AGENT_60_LIVE=1 to execute the real three-database Agent 60 runner")
    user_id = os.getenv("AGENT_60_USER_ID")
    fixture = seed_agent_60_live_fixture(user_id=user_id)
    try:
        yield fixture
    finally:
        teardown_agent_60_live_fixture(fixture)


def _write_report(case: str, payload: dict) -> None:
    report_path = Path(os.getenv("AGENT_60_REPORT_PATH", "/tmp/supplisense-agent60-live.json"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    if report_path.exists():
        current = json.loads(report_path.read_text(encoding="utf-8"))
    current[case] = payload
    report_path.write_text(json.dumps(current, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def test_agent_60_live_q46_cancel_is_durable_and_idempotent(agent_60_live_fixture) -> None:
    fixture = agent_60_live_fixture
    token = create_access_token({"sub": fixture.user_id})
    role_tokens = {
        role: create_access_token({"sub": user_id})
        for role, user_id in fixture.role_user_ids
    }
    from app.main import app

    base_url = os.getenv("AGENT_60_LIVE_BASE_URL")
    client_context = (
        httpx.Client(base_url=base_url, timeout=float(os.getenv("AGENT_60_REQUEST_TIMEOUT_SECONDS", "30")))
        if base_url
        else TestClient(app)
    )
    with client_context as client:
        response = client.post(
            "/api/v1/chat/stream",
            json={
                "message": "停止刚才正在进行的复核。",
                "session_id": fixture.session_id,
                "mode": "auto",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert '"status": "cancelled"' in response.text
    first = get_run(fixture.active_run_id)
    assert first is not None
    assert first["status"] == "CANCELLED"
    events = list_events_after(fixture.active_run_id, 0)
    assert events[-1]["event_type"] == "done"
    assert events[-1]["payload"]["status"] == "cancelled"

    # A second cancel is idempotent and does not append another event.
    repeated = cancel_active_harness_run(fixture.session_id, fixture.user_id, "buyer")
    assert repeated is not None
    assert repeated["status"] == "CANCELLED"
    assert len(list_events_after(fixture.active_run_id, 0)) == len(events)
    _write_report("Q46", {"status": "cancelled", "event_count": len(events)})


def test_agent_60_live_q47_interrupt_claim_release_and_ack_are_durable(agent_60_live_fixture) -> None:
    fixture = agent_60_live_fixture
    claimed = claim_chat_interrupt(fixture.session_id, fixture.user_id)
    assert claimed is not None
    assert claimed["config"]["__resume_value"] == {"approved": True}
    assert release_chat_interrupt(fixture.session_id, claimed["claim_token"]) is True

    retried = claim_chat_interrupt(fixture.session_id, fixture.user_id)
    assert retried is not None
    assert ack_chat_interrupt(fixture.session_id, retried["claim_token"]) is True
    assert claim_chat_interrupt(fixture.session_id, fixture.user_id) is None
    _write_report("Q47", {"status": "resumable", "claim_released": True, "claim_acked": True})


def test_agent_60_live_q60_http_sse_replays_after_last_event_id(agent_60_live_fixture, app) -> None:
    fixture = agent_60_live_fixture
    token = create_access_token({"sub": fixture.user_id})
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/chat/runs/{fixture.refreshable_run_id}/events",
            headers={"Authorization": f"Bearer {token}", "Last-Event-ID": "0"},
        )
    assert response.status_code == 200
    assert "event: done" in response.text
    assert fixture.marker in response.text
    assert get_run(fixture.refreshable_run_id)["status"] == "COMPLETED"
    _write_report("Q60", {"status": "replayed", "last_event_id": 1})


def test_agent_60_live_manifest_http_runner(agent_60_live_fixture, app) -> None:
    """Optional full manifest execution entrypoint for a provisioned E2E stack."""
    if not os.getenv("RUN_AGENT_60_LIVE_ALL"):
        pytest.skip("set RUN_AGENT_60_LIVE_ALL=1 after provisioning the complete Agent 60 fixture")
    fixture = agent_60_live_fixture
    token = create_access_token({"sub": fixture.user_id})
    role_tokens = {
        role: create_access_token({"sub": user_id})
        for role, user_id in fixture.role_user_ids
    }
    configured_ids = {
        item.strip()
        for item in os.getenv("AGENT_60_CASE_IDS", "").split(",")
        if item.strip()
    } or None
    base_url = os.getenv("AGENT_60_LIVE_BASE_URL")
    client_context = (
        httpx.Client(base_url=base_url, timeout=float(os.getenv("AGENT_60_REQUEST_TIMEOUT_SECONDS", "30")))
        if base_url
        else TestClient(app)
    )
    with client_context as client:
        results = run_agent_60_http_cases(
            client,
            token,
            marker=fixture.marker,
            case_ids=configured_ids,
            session_ids={"Q46": fixture.session_id, "Q47": fixture.session_id},
            access_tokens=role_tokens,
            assert_expected=os.getenv("AGENT_60_ASSERT_EXPECTED", "1") == "1",
        )
    assert results
    passed = sum(1 for result in results if result.get("passed"))
    if os.getenv("AGENT_60_ASSERT_EXPECTED", "1") == "1":
        assert passed == len(results)
    _write_report("manifest", {"total": len(results), "passed": passed, "failed": len(results) - passed, "results": results})
