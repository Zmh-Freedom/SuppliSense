"""Production Harness E2E against the real PostgreSQL/MongoDB/Redis seams."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.cache import cache_client
from app.core.security import create_access_token
from app.db.init_pg import ensure_pg_schema
from app.db.mongo import get_db
from app.db.postgres import get_cursor
from app.domains.agent_run.repo import get_run
from app.domains.agent_run.state_store import session_state_store
from app.evals.agent_harness_release import (
    assert_persisted_harness_quality,
    collect_persisted_harness_metrics,
)
from app.graphs.sourcing_risk_v2.checkpointer import get_sourcing_risk_checkpointer
from app.graphs.streaming import stream_harness_graph


pytestmark = [pytest.mark.integration, pytest.mark.agent_e2e, pytest.mark.agent_e2e_live]


def _read_sse(event: str) -> tuple[str, dict]:
    event_type = next(line.split(": ", 1)[1] for line in event.splitlines() if line.startswith("event: "))
    data_line = next(line.split(": ", 1)[1] for line in event.splitlines() if line.startswith("data: "))
    return event_type, json.loads(data_line)


@pytest.fixture
def production_harness_context():
    """Create isolated real DB state for one production-runtime run."""
    ensure_pg_schema()
    marker = f"task19-{uuid4().hex}"
    user_id = str(uuid4())
    session_id = str(uuid4())
    supplier_id = str(uuid4())
    db = get_db()
    now = datetime.now(timezone.utc)

    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            INSERT INTO users (id, username, email, password_hash, role)
            VALUES (%s, %s, %s, %s, 'admin')
            """,
            (user_id, marker, f"{marker}@example.test", "task19-test-hash"),
        )

    db["suppliers"].insert_one({
        "_id": supplier_id,
        "name": f"{marker}正式供应商",
        "status": "approved",
        "source": "task19",
        "created_at": now,
        "updated_at": now,
    })
    db["supplier_master_snapshots"].insert_one({
        "_id": f"{marker}-snapshot",
        "supplier_id": supplier_id,
        "supplier_code": f"{marker}-code",
        "name": f"{marker}正式供应商",
        "status": "active",
        "source": "feishu_bitable",
        "sync_status": "current",
        "source_record_id": marker,
        "synced_at": now,
    })

    context = {
        "history": [],
        "references": [],
        "conversation_state": {},
        "current_task": {
            "task_id": marker,
            "task_type": "sourcing",
            "user_message": "查询当前正式供应商",
            "target_supplier_names": [],
            "analysis_dimensions": [],
            "subtasks": [],
        },
    }
    started = session_state_store.start_harness_turn(
        session_id,
        user_id,
        "查询当前正式供应商",
        state={"execution_context": context},
    )
    yield {
        "marker": marker,
        "user_id": user_id,
        "session_id": session_id,
        "run_id": str(started["run"].id),
        "turn_id": str(started["turn"].id),
        "supplier_name": f"{marker}正式供应商",
        "supplier_id": supplier_id,
        "context": context,
    }

    for collection_name in (
        "suppliers", "supplier_master_snapshots", "supplier_capability_snapshots",
        "supplier_contact_snapshots", "conversations",
    ):
        query = {"source": "task19"} if collection_name == "suppliers" else {"source_record_id": marker}
        if collection_name == "conversations":
            query = {"session_id": session_id}
        db[collection_name].delete_many(query)
    with get_cursor() as (_, cursor):
        cursor.execute("DELETE FROM agent_sessions WHERE id = %s", (session_id,))
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))


async def _test_production_harness_sourcing_persists_real_run_artifacts(production_harness_context) -> None:
    context = production_harness_context
    checkpointer = await get_sourcing_risk_checkpointer()
    events: list[tuple[str, dict]] = []

    async for event in stream_harness_graph(
        "查询当前正式供应商",
        context["session_id"],
        context["context"],
        user_id=context["user_id"],
        run_id=context["run_id"],
        turn_id=context["turn_id"],
        checkpointer=checkpointer,
        run_config={"configurable": {"thread_id": context["run_id"]}},
    ):
        events.append(_read_sse(event))

    event_types = [event_type for event_type, _ in events]
    assert any(event_type == "done" for event_type, _ in events), events
    done = next(payload for event_type, payload in events if event_type == "done")
    assert done["status"] == "completed"
    assert {"tool_call", "tool_result", "agent_answer", "evidence", "done"}.issubset(event_types)

    metrics = collect_persisted_harness_metrics(context["run_id"])
    assert_persisted_harness_quality(metrics)
    assert metrics.run_status == "COMPLETED"
    assert metrics.tool_call_count == 1
    assert metrics.persisted_task_count == 1
    assert metrics.persisted_tool_call_count == 1
    assert metrics.snapshot_tool_call_count == 1
    assert metrics.tool_call_event_count == 1
    assert metrics.tool_result_event_count == 1
    assert metrics.artifact_consistent is True
    assert metrics.persisted_evidence_count >= 2
    assert metrics.evidence_coverage_ratio == 1.0
    assert metrics.unresolved_tool_calls == 0

    run = get_run(context["run_id"])
    assert run and run["requirement"]["answer"]["status"] == "completed"
    assert context["supplier_name"] in run["requirement"]["answer"]["summary"] or any(
        context["supplier_name"] in claim.get("statement", "")
        for claim in run["requirement"]["answer"].get("claims", [])
    )

    redis_key = f"{context['marker']}:redis"
    assert cache_client.set(redis_key, "ok", ex=30) is True
    assert cache_client.get(redis_key) == "ok"
    cache_client.delete(redis_key)


def test_production_harness_sourcing_persists_real_run_artifacts(production_harness_context) -> None:
    asyncio.run(_test_production_harness_sourcing_persists_real_run_artifacts(production_harness_context))


async def _test_production_harness_missing_data_finishes_needs_review(
    production_harness_context,
) -> None:
    """A real production tool gap must persist as needs_review, never low risk."""
    context = production_harness_context
    marker = context["marker"]
    missing_context = {
        "history": [],
        "references": [],
        "conversation_state": {},
        "current_task": {
            "task_id": f"{marker}-missing",
            "task_type": "analysis",
            "user_message": "分析不存在企业的质量风险",
            "target_supplier_names": [f"{marker}不存在企业"],
            "analysis_dimensions": ["quality"],
            "subtasks": [],
        },
    }
    started = session_state_store.start_harness_turn(
        context["session_id"],
        context["user_id"],
        "分析不存在企业的质量风险",
        state={"execution_context": missing_context},
    )
    run_id = str(started["run"].id)
    checkpointer = await get_sourcing_risk_checkpointer()
    events: list[tuple[str, dict]] = []

    async for event in stream_harness_graph(
        "分析不存在企业的质量风险",
        context["session_id"],
        missing_context,
        user_id=context["user_id"],
        run_id=run_id,
        turn_id=str(started["turn"].id),
        checkpointer=checkpointer,
        run_config={"configurable": {"thread_id": run_id}},
    ):
        events.append(_read_sse(event))

    assert any(event_type == "done" for event_type, _ in events), events
    done = next(payload for event_type, payload in events if event_type == "done")
    assert done["status"] == "needs_review"
    metrics = collect_persisted_harness_metrics(run_id)
    assert_persisted_harness_quality(metrics)
    assert metrics.run_status == "NEEDS_REVIEW"
    assert metrics.persisted_evidence_count == 0
    assert metrics.evidence_coverage_ratio == 0.0
    assert metrics.claim_count == 0
    assert metrics.artifact_consistent is True


def test_production_harness_missing_data_finishes_needs_review(production_harness_context) -> None:
    asyncio.run(_test_production_harness_missing_data_finishes_needs_review(production_harness_context))


def test_production_harness_http_sse_persists_server_terminal_state(
    production_harness_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The authenticated FastAPI/SSE boundary must expose the same durable terminal state."""
    from app.graphs.agent_core import adapter
    from app.main import app

    base_context = production_harness_context["context"]
    context = {
        **base_context,
        "references": [{
            "name": production_harness_context["supplier_name"],
            "supplier_id": production_harness_context["supplier_id"],
        }],
        "conversation_state": {
            "active_suppliers": [{"name": production_harness_context["supplier_name"]}],
            "selected_supplier_names": [production_harness_context["supplier_name"]],
        },
    }
    monkeypatch.setattr(adapter, "load_execution_context", lambda *_args: context)
    token = create_access_token({"sub": production_harness_context["user_id"]})

    @asynccontextmanager
    async def no_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": "查询当前正式供应商",
                "session_id": production_harness_context["session_id"],
                "mode": "auto",
            },
        )

    assert response.status_code == 200
    blocks = [
        block for block in response.text.split("\n\n")
        if any(line.startswith("event: ") for line in block.splitlines())
    ]
    event_types = [
        next(line.removeprefix("event: ") for line in block.splitlines() if line.startswith("event: "))
        for block in blocks
    ]
    assert "done" in event_types, response.text
    done_block = next(block for block in blocks if "event: done" in block.splitlines())
    done_payload = json.loads(next(line[6:] for line in done_block.splitlines() if line.startswith("data: ")))
    assert done_payload["status"] == "completed"
    assert "tool_call" in event_types
    assert "agent_answer" in event_types
    assert event_types[-1] == "done"
    run_id = done_payload["run_id"]
    metrics = collect_persisted_harness_metrics(run_id)
    assert_persisted_harness_quality(metrics)
    assert metrics.artifact_consistent is True

    # Simulate a browser reconnect after the first persisted event. The replay
    # endpoint must honor the cursor and still deliver the durable terminal event.
    with TestClient(app) as client:
        replay = client.get(
            f"/api/v1/chat/runs/{run_id}/events",
            headers={
                "Authorization": f"Bearer {token}",
                "Last-Event-ID": "1",
            },
        )

    assert replay.status_code == 200
    replay_blocks = [
        block for block in replay.text.split("\n\n")
        if any(line.startswith("event: ") for line in block.splitlines())
    ]
    replay_ids = [
        int(next(line[4:] for line in block.splitlines() if line.startswith("id: ")).strip())
        for block in replay_blocks
    ]
    assert replay_ids and min(replay_ids) > 1
    assert any("event: done" in block.splitlines() for block in replay_blocks)


def test_task19_fixture_residual_is_not_exposed_as_formal_supplier() -> None:
    """Interrupted Task19 fixtures must not pollute the formal supplier directory."""
    residual = get_db()["supplier_master_snapshots"].find_one(
        {"source_record_id": {"$regex": r"^task19-"}},
        {"_id": 1, "source_record_id": 1},
    )
    assert residual is None, residual
