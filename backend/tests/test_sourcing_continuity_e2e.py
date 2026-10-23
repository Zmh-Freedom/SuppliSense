"""Cross-page sourcing continuity through real services, LangGraph nodes, and Harness."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.domains.agent_run import service as agent_run_service
from app.domains.sourcing import workspace_service
from app.domains.sourcing_risk import handoff_service
from app.domains.sourcing_risk.policy_service import resolve_policy_template
from app.graphs.agent_core import adapter
from app.graphs.agent_core.evidence_ledger import build_evidence_record
from app.graphs.harness import ExecutionBudget, run_harness
from app.graphs.sourcing_risk_v2 import nodes, runner
from app.graphs.sourcing_risk_v2.state import SourcingRiskGraphState
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec

pytestmark = pytest.mark.agent_e2e

RUN_ID = "00000000-0000-4000-8000-000000000101"
USER_ID = "00000000-0000-0000-0000-000000000011"
CANDIDATE_A = "00000000-0000-4000-8000-000000000102"
CANDIDATE_B = "00000000-0000-4000-8000-000000000103"
COMPANY_A = "00000000-0000-4000-8000-000000000104"
COMPANY_B = "00000000-0000-4000-8000-000000000105"
HANDOFF_ID = "00000000-0000-4000-8000-000000000106"
SESSION_ID = "00000000-0000-4000-8000-000000000107"
CHAT_RUN_ID = "00000000-0000-4000-8000-000000000108"


class _JourneyStore:
    def __init__(self) -> None:
        self.run: dict[str, Any] = {
            "id": RUN_ID,
            "run_type": "sourcing_risk_v2",
            "user_id": USER_ID,
            "status": "IDENTITY_REVIEW",
            "version": 3,
            "requirement": {"requirement_text": "采购摄像头", "category": "摄像头"},
            "updated_at": datetime.now(timezone.utc),
        }
        self.candidates: list[dict[str, Any]] = []
        self.dispositions: dict[str, dict[str, Any]] = {}
        self.batches: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, Any]] = []


def _install_memory_repository(monkeypatch: pytest.MonkeyPatch, store: _JourneyStore) -> None:
    @contextmanager
    def cursor_context():
        yield object(), object()

    def persist_snapshot(_run_id: str, status: str, event_type: str, payload: dict[str, Any], **collections: Any):
        candidates = collections.get("candidates")
        if isinstance(candidates, list):
            existing = {str(item.get("id") or item.get("candidate_id")): item for item in store.candidates}
            for item in candidates:
                key = str(item.get("id") or item.get("candidate_id"))
                existing[key] = {**existing.get(key, {}), **item}
            store.candidates = list(existing.values())
        snapshot = {"status": status, "event_type": event_type, "payload": payload, **collections}
        store.snapshots.append(snapshot)
        store.run["status"] = status
        store.run["updated_at"] = datetime.now(timezone.utc)
        return {"candidates": list(store.candidates)}

    def update_status(_run_id: str, expected_version: int, status: str, **_kwargs: Any) -> dict[str, Any] | None:
        if store.run["version"] != expected_version:
            return None
        store.run["status"] = status
        store.run["version"] += 1
        store.run["updated_at"] = datetime.now(timezone.utc)
        return dict(store.run)

    monkeypatch.setattr(agent_run_service, "get_cursor", cursor_context)
    monkeypatch.setattr(agent_run_service, "get_run_for_update", lambda *_args: store.run)
    monkeypatch.setattr(agent_run_service, "list_candidates_with_cursor", lambda *_args: list(store.candidates))
    monkeypatch.setattr(
        agent_run_service,
        "list_sourcing_candidate_dispositions_with_cursor",
        lambda *_args: list(store.dispositions.values()),
    )
    monkeypatch.setattr(agent_run_service, "lock_candidate_review_request_with_cursor", lambda *_args: None)
    monkeypatch.setattr(agent_run_service, "get_candidate_review_event_by_request_with_cursor", lambda *_args: None)
    monkeypatch.setattr(
        agent_run_service,
        "upsert_sourcing_candidate_disposition_with_cursor",
        lambda **kwargs: store.dispositions.__setitem__(kwargs["candidate_id"], dict(kwargs)) or dict(kwargs),
    )
    monkeypatch.setattr(agent_run_service, "update_run_status", update_status)
    monkeypatch.setattr(
        agent_run_service,
        "append_event",
        lambda run_id, version, event_type, payload, **_kwargs: store.events.append({
            "run_id": run_id, "version": version, "event_type": event_type, "payload": payload,
        }),
    )

    def insert_batch_with_cursor(**kwargs: Any) -> dict[str, Any]:
        batch = dict(kwargs)
        batch_id = str(kwargs["batch_id"])
        store.batches[batch_id] = batch
        return batch

    def get_batch(batch_id: str) -> dict[str, Any] | None:
        batch = store.batches.get(batch_id)
        if batch is None:
            return None
        return {
            **batch,
            "dispatch_status": batch.get("dispatch_status"),
            "run_status": store.run["status"],
            "current_run_version": store.run["version"],
        }

    def mark_batch_consumed(_batch_id: str, _fencing_token: int, _cur: Any) -> bool:
        batch = store.batches.get(_batch_id)
        if batch is None or batch.get("dispatch_status") != "processing":
            return False
        batch["dispatch_status"] = "consumed"
        return True

    monkeypatch.setattr(agent_run_service, "insert_candidate_review_event_with_cursor", insert_batch_with_cursor)
    monkeypatch.setattr(agent_run_service, "repo_get_candidate_review_batch_for_execution", get_batch)
    monkeypatch.setattr(agent_run_service, "mark_candidate_review_batch_consumed_with_cursor", mark_batch_consumed)
    monkeypatch.setattr(agent_run_service, "persist_orchestration_snapshot", persist_snapshot)
    monkeypatch.setattr(
        agent_run_service,
        "record_orchestration_state",
        lambda _run_id, status, event_type, payload: (
            store.run.update({
                "status": status,
                "version": store.run["version"] + 1,
                "updated_at": datetime.now(timezone.utc),
            })
            or store.events.append({"status": status, "event_type": event_type, "payload": payload})
            or status
        ),
    )
    monkeypatch.setattr(
        agent_run_service,
        "append_orchestration_event",
        lambda _run_id, event_type, payload: store.events.append({"event_type": event_type, "payload": payload}),
    )


def _review_graph_builder(checkpointer: Any) -> Any:
    graph = StateGraph(SourcingRiskGraphState)
    graph.add_node("identity_review", nodes.identity_review)
    graph.add_node("investigate_parallel", nodes.investigate_parallel)
    graph.add_node("validate_evidence", nodes.validate_evidence)
    graph.add_node("score_candidates", nodes.score_candidates)
    graph.add_node("ready_for_review", nodes.ready_for_review)
    graph.add_edge(START, "identity_review")
    graph.add_conditional_edges(
        "identity_review",
        nodes.route_after_identity_review,
        {"end": END, "investigate_parallel": "investigate_parallel"},
    )
    graph.add_edge("investigate_parallel", "validate_evidence")
    graph.add_edge("validate_evidence", "score_candidates")
    graph.add_edge("score_candidates", "ready_for_review")
    graph.add_edge("ready_for_review", END)
    return graph.compile(checkpointer=checkpointer)


def test_partial_sourcing_candidate_handoff_to_harness_returns_evidence_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.agent_run import service as service_module
    from app.domains.sourcing_risk import handoff_repo

    store = _JourneyStore()
    _install_memory_repository(monkeypatch, store)
    policy = resolve_policy_template("摄像头")
    exact_candidate = {
        "id": CANDIDATE_A,
        "candidate_id": CANDIDATE_A,
        "candidate_key": CANDIDATE_A,
        "supplier_name": "甲工厂有限公司",
        "source": "formal_local",
        "company_id": COMPANY_A,
        "identity_status": "exact",
        "score_eligible": True,
        "identity_company_id": COMPANY_A,
        "active": True,
        "categories": ["摄像头"],
        "dimension_scores": {
            "match": 92, "capacity": 90, "performance": 88,
            "quality": 91, "risk": 90, "commercial": 89,
        },
    }
    pending_candidate = {
        "id": CANDIDATE_B,
        "candidate_id": CANDIDATE_B,
        "candidate_key": CANDIDATE_B,
        "supplier_name": "乙工厂（待核验）",
        "source": "staged_external",
        "company_id": None,
        "identity_status": "pending_verification",
        "score_eligible": False,
        "identity_candidates": [{"company_id": COMPANY_B, "supplier_name": "乙工厂有限公司"}],
    }
    store.candidates = [exact_candidate, pending_candidate]
    monkeypatch.setattr(
        nodes,
        "resolve_candidate_identity",
        lambda candidate: (
            {"identity_status": "exact", "company_id": COMPANY_A, "identity_company_id": COMPANY_A, "score_eligible": True}
            if candidate["candidate_id"] == CANDIDATE_A else
            {"identity_status": "ambiguous", "company_id": None, "identity_candidates": [{"company_id": COMPANY_B, "supplier_name": "乙工厂有限公司"}]}
        ),
    )

    provider_calls: list[str] = []

    async def synthetic_provider(candidate: dict[str, Any]) -> dict[str, Any]:
        provider_calls.append(str(candidate.get("candidate_id") or candidate.get("id") or ""))
        now = datetime.now(timezone.utc).isoformat()
        return {
            "claim_code": "clear",
            "source_type": "fixed_e2e_fixture",
            "source_reference": "fixture://sourcing/clear",
            "observed_at": now,
            "collected_at": now,
            "confidence": 0.95,
            "conflict_status": "none",
            "summary": "固定虚构来源：当前可核验",
        }

    for dimension in nodes.PROVIDER_DIMENSIONS:
        monkeypatch.setattr(nodes, f"fetch_{dimension}", synthetic_provider)

    saver = MemorySaver()
    resume_graph = _review_graph_builder(saver)
    monkeypatch.setattr(runner, "get_sourcing_risk_checkpointer", lambda: asyncio.sleep(0, result=saver))
    monkeypatch.setattr(runner, "build_sourcing_risk_graph", lambda checkpointer: _review_graph_builder(checkpointer))
    monkeypatch.setattr(runner, "require_v2_execution", lambda *_args: None)
    monkeypatch.setattr(runner, "get_orchestration_run", lambda _run_id: dict(store.run))

    async def journey() -> dict[str, Any]:
        identity = await nodes.identity_resolution({
            "run_id": RUN_ID,
            "candidates": list(store.candidates),
        })
        assert identity["status"] == "IDENTITY_REVIEW"
        assert identity["pending_review_ids"] == ["乙工厂（待核验）"]
        paused = await resume_graph.ainvoke(
            {
                **identity,
                "run_id": RUN_ID,
                "requirement": dict(store.run["requirement"]),
                "policy_snapshot": policy,
            },
            {"configurable": {"thread_id": RUN_ID}},
        )
        assert paused["__interrupt__"][0].value["pending_review_ids"] == ["乙工厂（待核验）"]

        submitted = service_module.submit_candidate_decisions(
            RUN_ID,
            {
                "expected_version": 3,
                "decisions": [{"candidate_key": CANDIDATE_B, "action": "defer", "reason": "等待主体资料"}],
            },
            USER_ID,
            "purchaser",
            "sourcing-continuity-e2e-review",
        )
        assert submitted["resume_status"] == "pending"
        assert store.dispositions[CANDIDATE_B]["action"] == "defer"
        batch_id = str(next(iter(store.batches)))
        store.batches[batch_id]["dispatch_status"] = "processing"
        store.batches[batch_id]["fencing_token"] = 1
        resumed = await runner.resume_candidate_review_batch(batch_id)
        assert resumed["status"] == "consumed"

        monkeypatch.setattr(workspace_service, "_run_source_state", lambda *_args: "available")
        monkeypatch.setattr(
            workspace_service,
            "list_sourcing_risk_runs_with_cursor",
            lambda **_kwargs: [{
                "source_type": "agent_run", "source_id": RUN_ID, "title": "采购摄像头",
                "status": store.run["status"], "updated_at": store.run["updated_at"],
                "resume_url": f"/sourcing?run_id={RUN_ID}",
            }],
        )
        monkeypatch.setattr(
            workspace_service,
            "list_requests_with_cursor",
            lambda **_kwargs: [{
                "source_type": "legacy_request", "source_id": "legacy-request-e2e",
                "title": "历史兼容任务", "status": "completed", "updated_at": store.run["updated_at"],
                "resume_url": "/sourcing/requests/legacy-request-e2e",
            }],
        )
        history = workspace_service.list_sourcing_tasks(USER_ID, "purchaser", cursor=None, limit=20)
        run_history = next(item for item in history["items"] if item["source_id"] == RUN_ID)
        assert run_history["status"] == "PARTIAL"
        assert {item["source_type"] for item in history["items"]} == {"agent_run", "legacy_request"}

        current_candidates = {str(item["candidate_id"]): item for item in store.candidates}
        source = {
            "run": {
                **store.run,
                "policy": policy,
            },
            "candidate": current_candidates[CANDIDATE_A],
            "disposition": None,
            "evidence_refs": [{
                "evidence_id": "sourcing-continuity-source-evidence",
                "dimension": "sanctions",
                "source_type": "fixed_e2e_fixture",
                "source_reference": "fixture://sourcing/sanctions-clear",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "freshness_status": "unknown",
                "claim_code": "clear",
            }],
        }
        monkeypatch.setattr(handoff_repo, "get_handoff_for_request", lambda *_args: None)
        monkeypatch.setattr(handoff_repo, "read_handoff_source", lambda *_args: source)
        monkeypatch.setattr("app.domains.supplier.access.formal_supplier_id_by_name", lambda _name: "formal-supplier-e2e")
        monkeypatch.setattr("app.domains.supplier.access.can_access_supplier", lambda *_args: True)
        handoff_access_checks: list[tuple[str, str, str, str]] = []
        authorize_candidate = handoff_service.can_access_handoff_candidate

        def authorize_fixture_candidate(
            company_id: str, supplier_name: str, user_id: str, user_role: str,
        ) -> bool:
            handoff_access_checks.append((company_id, supplier_name, user_id, user_role))
            return authorize_candidate(company_id, supplier_name, user_id, user_role)

        monkeypatch.setattr(handoff_service, "can_access_handoff_candidate", authorize_fixture_candidate)
        binding = {
            "handoff_id": HANDOFF_ID,
            "user_id": USER_ID,
            "session_id": SESSION_ID,
            "source_run_id": RUN_ID,
            "source_run_version": store.run["version"],
            "candidate_key": CANDIDATE_A,
            "candidate": dict(source["candidate"]),
            "requirement": dict(store.run["requirement"]),
            "source_policy": policy,
            "evidence_refs": [],
            "intent": "risk_review",
            "status": "ready",
            "continue_message": "请核验甲工厂有限公司并继续风险检查",
            "consumed_turn_id": None,
            "run_id": None,
        }
        created_transaction: dict[str, Any] = {}

        def create_handoff_transaction(*args: Any) -> dict[str, Any]:
            created_transaction["source"] = args[0]
            created_transaction["payload"] = args[1]
            created_transaction["user_id"] = args[2]
            created_transaction["request_key"] = args[4]
            return {"handoff_id": HANDOFF_ID, "session_id": SESSION_ID}

        monkeypatch.setattr(handoff_repo, "create_handoff_transaction", create_handoff_transaction)
        created = handoff_service.create_candidate_handoff(
            {
                "run_id": RUN_ID,
                "candidate_key": CANDIDATE_A,
                "expected_version": store.run["version"],
                "intent": "risk_review",
            },
            USER_ID,
            "purchaser",
            "sourcing-continuity-e2e-handoff",
        )
        assert created["handoff_id"] == HANDOFF_ID
        assert created_transaction["payload"]["candidate_key"] == CANDIDATE_A
        assert created_transaction["source"]["candidate"]["company_id"] == COMPANY_A
        monkeypatch.setattr(handoff_repo, "get_handoff_for_user", lambda *_args: binding)
        detail = handoff_service.get_candidate_handoff(HANDOFF_ID, USER_ID)
        assert detail is not None and detail["candidate_key"] == CANDIDATE_A
        monkeypatch.setattr(handoff_repo, "get_handoff_by_session", lambda _session_id: binding)

        context = adapter.build_execution_context(
            session_id=SESSION_ID,
            user_message=binding["continue_message"],
            references=[],
        )
        context.update({
            "agent_user_id": USER_ID,
            "agent_user_role": "purchaser",
            "_requested_handoff_id": HANDOFF_ID,
            "_handoff_request_message": binding["continue_message"],
        })
        resolved_context = handoff_service.resolve_session_handoff(SESSION_ID, USER_ID, context)
        assert resolved_context["current_task"]["capability"] == "risk"
        assert resolved_context["current_task"]["target_supplier_names"] == ["甲工厂有限公司"]
        assert resolved_context["sourcing_handoff"]["evidence_refs"][0]["freshness_status"] == "fresh"
        assert handoff_access_checks == [
            (COMPANY_A, "甲工厂有限公司", USER_ID, "purchaser"),
            (COMPANY_A, "甲工厂有限公司", USER_ID, "purchaser"),
        ]

        evidence = build_evidence_record(
            evidence_id="sourcing-continuity-risk-evidence",
            entity_id=COMPANY_A,
            dimension="risk",
            provider="fixed-e2e-risk-fixture",
            source_type="supplier_registry",
            payload={"risk_score": 0.14, "assessment": "clear"},
            collected_at=datetime.now(timezone.utc),
        ).model_dump(mode="json")
        read_calls: list[str] = []

        def review_risk(company_name: str) -> dict[str, Any]:
            read_calls.append(company_name)
            return {
                "status": "success",
                "risk_review_id": "risk-review-e2e-1",
                "evidence_records": [evidence],
                "claims": [{
                    "claim_id": "claim-sourcing-continuity-e2e",
                    "entity_id": COMPANY_A,
                    "dimension": "risk",
                    "statement": "当前风险检查已完成",
                    "value": 0.14,
                    "evidence_refs": ["sourcing-continuity-risk-evidence"],
                    "confidence": 0.94,
                }],
            }

        registry = ToolRegistry()
        registry.register(
            StructuredTool.from_function(review_risk, name="review_supplier_risk", description="读取当前供应商风险证据"),
            ToolSpec(
                name="review_supplier_risk", capability="risk", side_effect="read",
                approval_policy="none", evidence_required=True,
            ),
        )
        monkeypatch.setattr(
            "app.domains.auth.service.get_user_by_id",
            lambda _user_id: type("Purchaser", (), {"role": type("Role", (), {"value": "purchaser"})()})(),
        )
        harness_result = await run_harness({
            "session_id": SESSION_ID,
            "turn_id": "turn-sourcing-continuity-e2e",
            "run_id": CHAT_RUN_ID,
            "user_id": USER_ID,
            "user_message": binding["continue_message"],
            "execution_context": resolved_context,
            "current_task": resolved_context["current_task"],
            "task_specs": [{
                "task_id": "risk-check-e2e",
                "tool_name": "review_supplier_risk",
                "arguments": {"company_name": "甲工厂有限公司"},
                "entity_id": COMPANY_A,
                "dimension": "risk",
                "required": True,
            }],
            "budget": ExecutionBudget().model_dump(mode="json"),
        }, executor=ToolExecutor(registry))
        return {
            "store": store,
            "history": history,
            "provider_calls": provider_calls,
            "read_calls": read_calls,
            "harness": harness_result,
        }

    result = asyncio.run(journey())

    assert result["store"].run["status"] == "PARTIAL"
    assert result["store"].dispositions[CANDIDATE_B]["action"] == "defer"
    assert sorted(result["provider_calls"]) == [CANDIDATE_A] * len(nodes.PROVIDER_DIMENSIONS)
    assert result["history"]["sources"] == {
        "agent_run": {"status": "available"},
        "legacy_request": {"status": "available"},
    }
    assert result["read_calls"] == ["甲工厂有限公司"]
    assert result["harness"]["answer"]["status"] == "completed"
    assert result["harness"]["tool_outcomes"][0]["status"] == "success"
    assert result["harness"]["tool_outcomes"][0]["data"]["risk_review_id"] == "risk-review-e2e-1"
    assert any(
        item["evidence_id"] == "sourcing-continuity-risk-evidence"
        for item in result["harness"]["evidence_records"]
    )
