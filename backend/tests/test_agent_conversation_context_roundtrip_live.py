"""Authenticated API round-trip across real isolated PostgreSQL/Mongo seams."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import StructuredTool

from app.core.config import settings
from app.core.security import create_access_token
from app.db.init_pg import ensure_pg_schema
from app.db.mongo import close_db, get_db
from app.db.postgres import close_pool, get_cursor
from app.domains.agent_run.state_store import session_state_store
from app.graphs.agent_core.evidence_ledger import EvidenceStatus, build_evidence_record
from app.tools.registry import ToolPayload, ToolRegistry, ToolSpec


pytestmark = [pytest.mark.integration, pytest.mark.agent_e2e_live]


def _sse_events(response_text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in response_text.split("\n\n"):
        event_line = next((line for line in block.splitlines() if line.startswith("event: ")), None)
        data_line = next((line for line in block.splitlines() if line.startswith("data: ")), None)
        if not event_line or not data_line:
            continue
        raw_data = data_line.removeprefix("data: ")
        try:
            payload = json.loads(raw_data)
        except json.JSONDecodeError:
            payload = {"value": raw_data}
        events.append((event_line.removeprefix("event: "), payload))
    return events


@pytest.fixture
def isolated_chat_databases(monkeypatch: pytest.MonkeyPatch):
    """Require explicit isolated PG/Mongo databases before any write fixture."""
    test_pg_database = os.environ.get("SUPPLISENSE_TEST_PG_DB")
    test_mongo_database = os.environ.get("SUPPLISENSE_TEST_MONGO_DB")
    if not test_pg_database or not test_mongo_database:
        pytest.skip("需要 SUPPLISENSE_TEST_PG_DB 和 SUPPLISENSE_TEST_MONGO_DB 才运行真实往返验收")
    if test_pg_database.lower() in {"sra", "tianyancha"}:
        pytest.fail("真实聊天往返禁止使用共享 PostgreSQL 开发库")
    if test_mongo_database.lower() in {"sra", "tianyancha"}:
        pytest.fail("真实聊天往返禁止使用共享 MongoDB 开发库")
    # The explicit fixture variable selects the target; reject shared DB names above,
    # then verify the actual connection with SELECT current_database() in the test.
    monkeypatch.setattr(settings, "PG_DB", test_pg_database)
    monkeypatch.setattr(settings, "MONGO_DB", test_mongo_database)
    close_pool()
    close_db()
    ensure_pg_schema()
    db = get_db()
    db.command("ping")
    if db.name != test_mongo_database:
        pytest.fail("MongoDB 未连接到显式测试数据库")
    yield {"pg_database": test_pg_database, "mongo_database": test_mongo_database, "mongo": db}
    close_db()
    close_pool()


@pytest.fixture
def controlled_chat_runtime(app, monkeypatch: pytest.MonkeyPatch):
    """Control only the LLM and business provider; retain API, context and Harness."""
    from app.graphs.agent_core import intent_extractor, narrator
    from app.graphs.harness import graph as harness_graph
    from app.graphs.sourcing_risk_v2 import checkpointer

    intent_payloads: dict[str, dict] = {}
    model_inputs: list[str] = []
    tool_calls: list[str] = []

    def query_financials(company_name: str) -> dict:
        tool_calls.append(company_name)
        evidence = build_evidence_record(
            evidence_id=f"synthetic-financial-{company_name}",
            entity_id=f"entity:{company_name}",
            dimension="financial",
            provider="test-only-fixture",
            source_type="synthetic_fixture",
            payload={"revenue": 1200000, "revenue_unit": "CNY", "fiscal_year": 2025},
            status=EvidenceStatus.SYNTHETIC,
            collected_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
            data_mode="synthetic",
        ).model_dump(mode="json")
        return {"status": "success", "evidence_records": [evidence], "claims": []}

    registry = ToolRegistry()
    registry.register(
        StructuredTool.from_function(
            query_financials,
            name="query_financials",
            description="Synthetic-only financial fixture for context round-trip acceptance.",
        ),
        ToolSpec(
            name="query_financials",
            capability="financial",
            side_effect="read",
            approval_policy="none",
            max_attempts=1,
            evidence_required=True,
        ),
        output_model=ToolPayload,
    )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        @staticmethod
        def _create(*, messages, **_kwargs):
            request = json.loads(messages[1]["content"])
            message = request["current_message"]
            model_inputs.append(message)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(intent_payloads[message], ensure_ascii=False))
            )])

    monkeypatch.setattr(settings, "LLM_API_KEY", "test-only-structured-intent")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(harness_graph, "TOOL_REGISTRY", registry)
    monkeypatch.setattr(narrator, "narrate_answer", lambda answer, _message: answer)

    async def no_checkpointer():
        return None

    monkeypatch.setattr(checkpointer, "get_sourcing_risk_checkpointer", no_checkpointer)

    @asynccontextmanager
    async def no_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)
    yield SimpleNamespace(
        intent_payloads=intent_payloads,
        model_inputs=model_inputs,
        tool_calls=tool_calls,
    )


def test_authenticated_chat_round_trips_null_task_and_next_query_through_postgres(
    isolated_chat_databases,
    controlled_chat_runtime,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.main import app

    marker = f"conversation-context-{uuid4().hex}"
    user_id = str(uuid4())
    session_id = str(uuid4())
    clarification_session_id = str(uuid4())
    fallback_clarification_session_id = str(uuid4())
    legacy_session_id = str(uuid4())
    ambiguous_session_id = str(uuid4())
    other_user_id = str(uuid4())
    supplier_id = str(uuid4())
    other_supplier_id = str(uuid4())
    supplier_name = f"{marker}甲供应商有限公司"
    other_supplier_name = f"{marker}乙供应商有限公司"
    greeting = "你好"
    query = f"分析{supplier_name}的财务风险"
    pronoun_query = "它的财务风险怎么样？"
    topic_switch_query = f"改问{other_supplier_name}的财务风险"
    missing_target_query = "分析一下财务风险"
    clarification_answer = f"分析{other_supplier_name}的财务风险"
    legacy_query = f"分析{supplier_name}的财务风险"
    ambiguous_alias_query = "分析三祥的财务风险"
    alias_confirmation_query = f"确认分析{supplier_name}的财务风险"
    db = isolated_chat_databases["mongo"]
    runtime = controlled_chat_runtime

    try:
        with get_cursor() as (_, cursor):
            cursor.execute("SELECT current_database()")
            assert cursor.fetchone()[0] == isolated_chat_databases["pg_database"]
            cursor.execute(
                """
                INSERT INTO users (id, username, email, password_hash, role)
                VALUES (%s, %s, %s, %s, 'admin'), (%s, %s, %s, %s, 'viewer')
                """,
                (
                    user_id, marker, f"{marker}@example.test", "test-only-password-hash",
                    other_user_id, f"{marker}-other", f"{marker}-other@example.test", "test-only-password-hash",
                ),
            )
            cursor.execute(
                """
                INSERT INTO supplier_responsibility_snapshots (
                    source_record_id, supplier_id, supplier_name, source_active,
                    sync_status, sync_batch_id
                ) VALUES (%s, %s, %s, TRUE, 'current', %s)
                """,
                (marker, supplier_id, supplier_name, str(uuid4())),
            )
            cursor.execute(
                """
                INSERT INTO supplier_responsibility_snapshots (
                    source_record_id, supplier_id, supplier_name, source_active,
                    sync_status, sync_batch_id
                ) VALUES (%s, %s, %s, TRUE, 'current', %s)
                """,
                (f"{marker}-other", other_supplier_id, other_supplier_name, str(uuid4())),
            )

        db["suppliers"].insert_one({
            "_id": supplier_id,
            "supplier_id": supplier_id,
            "source_record_id": marker,
            "name": supplier_name,
            "status": "approved",
            "source": "context-roundtrip-test",
        })
        db["supplier_master_snapshots"].insert_one({
            "_id": f"{marker}-snapshot",
            "supplier_id": supplier_id,
            "source_record_id": marker,
            "name": supplier_name,
            "status": "active",
            "source": "feishu_bitable",
            "sync_status": "current",
            "synced_at": datetime(2026, 10, 30, tzinfo=timezone.utc),
        })
        db["suppliers"].insert_one({
            "_id": other_supplier_id,
            "supplier_id": other_supplier_id,
            "source_record_id": f"{marker}-other",
            "name": other_supplier_name,
            "status": "approved",
            "source": "context-roundtrip-test",
        })
        db["supplier_master_snapshots"].insert_one({
            "_id": f"{marker}-other-snapshot",
            "supplier_id": other_supplier_id,
            "source_record_id": f"{marker}-other",
            "name": other_supplier_name,
            "status": "active",
            "source": "feishu_bitable",
            "sync_status": "current",
            "synced_at": datetime(2026, 10, 30, tzinfo=timezone.utc),
        })

        session_state_store.create_session(user_id, session_id=session_id)
        session_state_store.create_session(user_id, session_id=clarification_session_id)
        session_state_store.create_session(user_id, session_id=fallback_clarification_session_id)
        session_state_store.create_session(user_id, session_id=legacy_session_id)
        session_state_store.create_session(user_id, session_id=ambiguous_session_id)
        token = create_access_token({"sub": user_id})
        from app.graphs.agent_core import adapter

        original_loader = adapter.load_execution_context
        load_calls: list[str] = []

        def count_loader(sid: str, message: str, owner: str | None = None) -> dict:
            load_calls.append(message)
            return original_loader(sid, message, owner)

        monkeypatch.setattr(adapter, "load_execution_context", count_loader)
        with TestClient(app) as client:
            first = client.post(
                "/api/v1/chat/stream",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": greeting, "session_id": session_id, "mode": "auto"},
            )
            assert first.status_code == 200, first.text
            first_events = _sse_events(first.text)
            assert first_events[-1][0] == "done", first_events
            assert first_events[-1][1]["status"] == "completed"

            persisted_after_greeting = session_state_store.get_execution_context(session_id, user_id)
            assert persisted_after_greeting is not None
            assert persisted_after_greeting["history"][-2:] == [
                {"role": "user", "content": greeting},
                {"role": "assistant", "content": first_events[-1][1]["answer"]},
            ]
            assert persisted_after_greeting["conversation_state"]["current_task"] is None
            with pytest.raises(PermissionError, match="不属于当前用户"):
                session_state_store.get_execution_context(session_id, str(uuid4()))

            runtime.intent_payloads[query] = {
                "target_supplier_names": [supplier_name],
                "analysis_dimensions": ["financial"],
                "provider_capabilities": [],
                "capability": "financial",
                "scope": "single_supplier",
                "task_type": "analysis",
                "requested_action": "none",
                "confidence": 0.99,
                "interpretation_summary": "查询合成测试供应商的财务风险。",
                "field_sources": {
                    "target_supplier_names": "explicit",
                    "analysis_dimensions": "explicit",
                },
            }
            second = client.post(
                "/api/v1/chat/stream",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": query, "session_id": session_id, "mode": "auto"},
            )

            assert second.status_code == 200, second.text
            second_events = _sse_events(second.text)
            event_types = [event_type for event_type, _ in second_events]
            assert event_types[-1] == "done", second_events
            assert "tool_call" in event_types
            assert "tool_result" in event_types
            assert "evidence" in event_types
            assert runtime.tool_calls == [supplier_name]
            assert runtime.model_inputs == [query]
            assert load_calls == [greeting, query]
            evidence = next(payload for event_type, payload in second_events if event_type == "evidence")
            assert evidence["records"][0]["data_mode"] == "synthetic"
            assert evidence["records"][0]["status"] == "synthetic"

            persisted_after_query = session_state_store.get_execution_context(session_id, user_id)
            assert persisted_after_query is not None
            assert [item["content"] for item in persisted_after_query["history"][-4::2]] == [greeting, query]
            current_task = persisted_after_query["conversation_state"]["current_task"]
            assert current_task["task_type"] == "analysis"
            assert current_task["target_supplier_names"] == [supplier_name]
            assert current_task["user_message"] == query

            runtime.intent_payloads[pronoun_query] = {
                "target_supplier_names": [],
                "analysis_dimensions": ["financial"],
                "provider_capabilities": [],
                "capability": "financial",
                "scope": "single_supplier",
                "task_type": "analysis",
                "requested_action": "none",
                "confidence": 0.95,
                "interpretation_summary": "沿用上一轮明确的供应商目标。",
                "field_sources": {"analysis_dimensions": "explicit"},
            }
            pronoun_response = client.post(
                "/api/v1/chat/stream",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": pronoun_query, "session_id": session_id, "mode": "auto"},
            )
            assert pronoun_response.status_code == 200, pronoun_response.text
            pronoun_events = _sse_events(pronoun_response.text)
            assert pronoun_events[-1][0] == "done", pronoun_events
            assert runtime.tool_calls[-1] == supplier_name
            persisted_after_pronoun = session_state_store.get_execution_context(session_id, user_id)
            assert persisted_after_pronoun is not None
            assert persisted_after_pronoun["conversation_state"]["current_task"]["target_supplier_names"] == [supplier_name]

            runtime.intent_payloads[topic_switch_query] = {
                "target_supplier_names": [other_supplier_name],
                "analysis_dimensions": ["financial"],
                "provider_capabilities": [],
                "capability": "financial",
                "scope": "single_supplier",
                "task_type": "analysis",
                "requested_action": "none",
                "confidence": 0.99,
                "interpretation_summary": "切换到另一家合成测试供应商。",
                "field_sources": {
                    "target_supplier_names": "explicit",
                    "analysis_dimensions": "explicit",
                },
            }
            switch_response = client.post(
                "/api/v1/chat/stream",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": topic_switch_query, "session_id": session_id, "mode": "auto"},
            )
            assert switch_response.status_code == 200, switch_response.text
            switch_events = _sse_events(switch_response.text)
            assert switch_events[-1][0] == "done", switch_events
            assert runtime.tool_calls[-1] == other_supplier_name
            persisted_after_switch = session_state_store.get_execution_context(session_id, user_id)
            assert persisted_after_switch is not None
            switched_task = persisted_after_switch["conversation_state"]["current_task"]
            assert switched_task["target_supplier_names"] == [other_supplier_name]
            assert switched_task["user_message"] == topic_switch_query

            foreign_token = create_access_token({"sub": other_user_id})
            foreign_response = client.post(
                "/api/v1/chat/stream",
                headers={"Authorization": f"Bearer {foreign_token}"},
                json={"message": "读取这个会话", "session_id": session_id, "mode": "auto"},
            )
            assert foreign_response.status_code == 200, foreign_response.text
            foreign_events = _sse_events(foreign_response.text)
            assert any(event_type == "error" for event_type, _ in foreign_events), foreign_events
            assert supplier_name not in foreign_response.text
            assert other_supplier_name not in foreign_response.text
            assert runtime.tool_calls == [supplier_name, supplier_name, other_supplier_name]

            runtime.intent_payloads[missing_target_query] = {
                "target_supplier_names": [],
                "analysis_dimensions": ["financial"],
                "provider_capabilities": [],
                "capability": "financial",
                "scope": "single_supplier",
                "task_type": "analysis",
                "requested_action": "none",
                "confidence": 0.8,
                "interpretation_summary": "请求分析财务风险，但未给出供应商。",
                "needs_clarification": True,
                "missing_fields": ["target_supplier_names"],
                "clarification_question": "请补充供应商名称。",
                "field_sources": {"analysis_dimensions": "explicit"},
            }
            clarification_response = client.post(
                "/api/v1/chat/stream",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": missing_target_query, "session_id": clarification_session_id, "mode": "auto"},
            )
            assert clarification_response.status_code == 200, clarification_response.text
            clarification_events = _sse_events(clarification_response.text)
            assert any(event_type == "clarification" for event_type, _ in clarification_events)
            assert not any(event_type == "tool_call" for event_type, _ in clarification_events)
            pending_context = session_state_store.get_execution_context(clarification_session_id, user_id)
            assert pending_context is not None
            pending = pending_context["conversation_state"]["pending_clarification"]
            assert pending["missing_fields"] == ["target_supplier_names"]
            assert pending["analysis_dimensions"] == ["financial"]

            from app.graphs.agent_core import intent_extractor

            tools_before_fallback = list(runtime.tool_calls)
            model_inputs_before_fallback = list(runtime.model_inputs)
            with monkeypatch.context() as fallback_monkeypatch:
                fallback_monkeypatch.setattr(
                    intent_extractor,
                    "extract_conversation_intent",
                    lambda *_args, **_kwargs: None,
                )
                fallback_clarification_response = client.post(
                    "/api/v1/chat/stream",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "message": missing_target_query,
                        "session_id": fallback_clarification_session_id,
                        "mode": "auto",
                    },
                )
            assert fallback_clarification_response.status_code == 200, fallback_clarification_response.text
            fallback_clarification_events = _sse_events(fallback_clarification_response.text)
            assert any(
                event_type == "clarification"
                for event_type, _ in fallback_clarification_events
            ), fallback_clarification_events
            assert not any(
                event_type == "tool_call"
                for event_type, _ in fallback_clarification_events
            ), fallback_clarification_events
            assert runtime.tool_calls == tools_before_fallback
            assert runtime.model_inputs == model_inputs_before_fallback
            fallback_context = session_state_store.get_execution_context(
                fallback_clarification_session_id, user_id
            )
            assert fallback_context is not None
            fallback_pending = fallback_context["conversation_state"]["pending_clarification"]
            assert fallback_pending["missing_fields"] == ["target_supplier_names"]
            assert fallback_pending["analysis_dimensions"] == ["financial", "risk"]

            runtime.intent_payloads[clarification_answer] = {
                "target_supplier_names": [other_supplier_name],
                "analysis_dimensions": ["financial"],
                "provider_capabilities": [],
                "capability": "financial",
                "scope": "single_supplier",
                "task_type": "analysis",
                "requested_action": "none",
                "confidence": 0.99,
                "interpretation_summary": "补充供应商后继续财务分析。",
                "field_sources": {
                    "target_supplier_names": "explicit",
                    "analysis_dimensions": "explicit",
                },
            }
            clarification_followup = client.post(
                "/api/v1/chat/stream",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": clarification_answer, "session_id": clarification_session_id, "mode": "auto"},
            )
            assert clarification_followup.status_code == 200, clarification_followup.text
            clarification_followup_events = _sse_events(clarification_followup.text)
            assert clarification_followup_events[-1][0] == "done", clarification_followup_events
            assert runtime.tool_calls[-1] == other_supplier_name
            resolved_context = session_state_store.get_execution_context(clarification_session_id, user_id)
            assert resolved_context is not None
            assert "pending_clarification" not in resolved_context["conversation_state"]
            assert resolved_context["conversation_state"]["current_task"]["target_supplier_names"] == [other_supplier_name]

            legacy_query = f"分析{supplier_name}的财务风险"
            runtime.intent_payloads[legacy_query] = runtime.intent_payloads[query]
            legacy_context = {
                "history": [{"role": "user", "content": "你好"}],
                "references": [],
                "conversation_state": {
                    "current_task": {
                        "task_type": "none",
                        "target_supplier_names": [],
                        "analysis_dimensions": [],
                    },
                    "pending_approvals": [],
                },
            }
            with get_cursor() as (_, cursor):
                cursor.execute(
                    "UPDATE agent_sessions SET state = jsonb_build_object('execution_context', %s::jsonb) WHERE id = %s",
                    (json.dumps(legacy_context, ensure_ascii=False), legacy_session_id),
                )
            legacy_response = client.post(
                "/api/v1/chat/stream",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": legacy_query, "session_id": legacy_session_id, "mode": "auto"},
            )
            assert legacy_response.status_code == 200, legacy_response.text
            legacy_events = _sse_events(legacy_response.text)
            assert legacy_events[-1][0] == "done", legacy_events
            assert runtime.tool_calls[-1] == supplier_name
            migrated_context = session_state_store.get_execution_context(legacy_session_id, user_id)
            assert migrated_context is not None
            assert migrated_context["conversation_state"]["current_task"]["task_type"] == "analysis"
            assert migrated_context["conversation_state"]["current_task"]["target_supplier_names"] == [supplier_name]

            ambiguous_context = {
                "history": [],
                "references": [
                    {
                        "kind": "supplier",
                        "name": supplier_name,
                        "supplier_id": supplier_id,
                        "aliases": ["三祥"],
                        "source": "test_fixture",
                    },
                    {
                        "kind": "supplier",
                        "name": other_supplier_name,
                        "supplier_id": other_supplier_id,
                        "aliases": ["三祥"],
                        "source": "test_fixture",
                    },
                ],
                "conversation_state": {
                    "current_task": None,
                    "selected_supplier_names": [supplier_name, other_supplier_name],
                    "pending_approvals": [],
                },
            }
            assert session_state_store.update_execution_context(
                ambiguous_session_id, user_id, ambiguous_context
            ) is not None
            runtime.intent_payloads[ambiguous_alias_query] = {
                # Deliberately let the controlled model pick A. The stored
                # shared alias must override that suggestion and require user confirmation.
                "target_supplier_names": [supplier_name],
                "analysis_dimensions": ["financial"],
                "provider_capabilities": [],
                "capability": "financial",
                "scope": "single_supplier",
                "task_type": "analysis",
                "requested_action": "none",
                "needs_clarification": False,
                "missing_fields": [],
                "confidence": 0.99,
                "interpretation_summary": "模型候选为 A，但简称“三祥”对应两家供应商。",
                "field_sources": {
                    "target_supplier_names": "explicit",
                    "analysis_dimensions": "explicit",
                },
            }
            calls_before_alias = list(runtime.tool_calls)
            alias_response = client.post(
                "/api/v1/chat/stream",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": ambiguous_alias_query, "session_id": ambiguous_session_id, "mode": "auto"},
            )
            assert alias_response.status_code == 200, alias_response.text
            alias_events = _sse_events(alias_response.text)
            assert alias_events[-1][0] == "clarification", alias_events
            assert any(event_type == "clarification" for event_type, _ in alias_events), alias_events
            assert not any(event_type == "tool_call" for event_type, _ in alias_events), alias_events
            assert runtime.tool_calls == calls_before_alias
            pending_alias_context = session_state_store.get_execution_context(ambiguous_session_id, user_id)
            assert pending_alias_context is not None
            assert pending_alias_context["conversation_state"]["entity_resolution"]["reason"] == "ambiguous_alias"
            assert pending_alias_context["conversation_state"]["pending_clarification"]["missing_fields"] == [
                "target_supplier_names"
            ]
            assert pending_alias_context["conversation_state"]["pending_clarification"]["analysis_dimensions"] == [
                "financial"
            ]

            runtime.intent_payloads[alias_confirmation_query] = runtime.intent_payloads[query]
            alias_confirmation_response = client.post(
                "/api/v1/chat/stream",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "message": alias_confirmation_query,
                    "session_id": ambiguous_session_id,
                    "mode": "auto",
                },
            )
            assert alias_confirmation_response.status_code == 200, alias_confirmation_response.text
            alias_confirmation_events = _sse_events(alias_confirmation_response.text)
            assert alias_confirmation_events[-1][0] == "done", alias_confirmation_events
            assert any(event_type == "tool_call" for event_type, _ in alias_confirmation_events)
            assert runtime.tool_calls == [*calls_before_alias, supplier_name]
            confirmed_alias_context = session_state_store.get_execution_context(ambiguous_session_id, user_id)
            assert confirmed_alias_context is not None
            assert "pending_clarification" not in confirmed_alias_context["conversation_state"]
            assert confirmed_alias_context["conversation_state"]["current_task"]["target_supplier_names"] == [
                supplier_name
            ]

            assert load_calls == [
                greeting, query, pronoun_query, topic_switch_query, "读取这个会话",
                missing_target_query, missing_target_query, clarification_answer, legacy_query,
                ambiguous_alias_query, alias_confirmation_query,
            ]
    finally:
        test_session_ids = [
            session_id,
            clarification_session_id,
            fallback_clarification_session_id,
            legacy_session_id,
            ambiguous_session_id,
        ]
        db["conversations"].delete_many({"session_id": {"$in": test_session_ids}})
        assert db["conversations"].count_documents({"session_id": {"$in": test_session_ids}}) == 0
        db["suppliers"].delete_one({"_id": supplier_id})
        db["suppliers"].delete_one({"_id": other_supplier_id})
        db["supplier_master_snapshots"].delete_one({"_id": f"{marker}-snapshot"})
        db["supplier_master_snapshots"].delete_one({"_id": f"{marker}-other-snapshot"})
        db["user_preferences"].delete_one({"user_id": user_id})
        db["user_preferences"].delete_one({"user_id": other_user_id})
        with get_cursor() as (_, cursor):
            cursor.execute(
                "DELETE FROM agent_sessions WHERE id IN (%s, %s, %s, %s, %s)",
                (
                    session_id, clarification_session_id,
                    fallback_clarification_session_id, legacy_session_id, ambiguous_session_id,
                ),
            )
            cursor.execute(
                "DELETE FROM supplier_responsibility_snapshots WHERE source_record_id IN (%s, %s)",
                (marker, f"{marker}-other"),
            )
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            cursor.execute("DELETE FROM users WHERE id = %s", (other_user_id,))
            cursor.execute(
                "SELECT COUNT(*) FROM agent_sessions WHERE id IN (%s, %s, %s, %s, %s)",
                (
                    session_id, clarification_session_id,
                    fallback_clarification_session_id, legacy_session_id, ambiguous_session_id,
                ),
            )
            assert cursor.fetchone()[0] == 0
            cursor.execute(
                "SELECT COUNT(*) FROM users WHERE id IN (%s, %s)",
                (user_id, other_user_id),
            )
            assert cursor.fetchone()[0] == 0
            cursor.execute(
                "SELECT COUNT(*) FROM supplier_responsibility_snapshots WHERE source_record_id IN (%s, %s)",
                (marker, f"{marker}-other"),
            )
            assert cursor.fetchone()[0] == 0
        assert db["suppliers"].count_documents({"source_record_id": {"$in": [marker, f"{marker}-other"]}}) == 0
        assert db["supplier_master_snapshots"].count_documents({"source_record_id": {"$in": [marker, f"{marker}-other"]}}) == 0
