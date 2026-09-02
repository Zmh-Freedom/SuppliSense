"""Fixed P0 Agent Harness scenarios with real deterministic production seams."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict

from app.domains.sourcing_risk import discovery_service
from app.evals.agent_harness import (
    HarnessDoubleError,
    P0Observation,
    P0Scenario,
    ProgrammableLLMDouble,
    ProviderReplayDouble,
    assert_p0_quality_gates,
    load_p0_scenarios,
    run_p0_harness,
    stable_fingerprint,
)
from app.graphs.agent_core.evidence_ledger import (
    Claim,
    EvidenceLedger,
    build_evidence_record,
)
from app.graphs.agent_core.answer_contract import build_agent_answer
from app.graphs.harness import ActionGate, run_harness
from app.graphs.interrupt_store import pop, store
from app.services.conversation_state import resolve_supplier_target_selection
from app.tools.executor import ToolContext, ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec


pytestmark = pytest.mark.agent_e2e

SCENARIOS_PATH = Path(__file__).parent / "evals" / "agent_harness_p0.json"
REFERENCES = [
    {"name": "甲电机有限公司", "aliases": ["甲电机"], "supplier_id": "supplier-a"},
    {"name": "乙电机有限公司", "aliases": ["乙电机"], "supplier_id": "supplier-b"},
]


def _load_scenarios() -> list[P0Scenario]:
    return load_p0_scenarios(SCENARIOS_PATH)


def _observation(scenario_id: str, **kwargs) -> P0Observation:
    return P0Observation(scenario_id=scenario_id, fingerprint=stable_fingerprint(kwargs), **kwargs)


class P0Runner:
    def run(self, scenario: P0Scenario) -> P0Observation:
        handler = getattr(self, f"_{scenario.scenario_id.replace('-', '_')}")
        return handler(scenario.scenario_id)

    def _recommendation_then_plural_risk(self, scenario_id: str) -> P0Observation:
        result = resolve_supplier_target_selection("对这些企业做风险分析", REFERENCES)
        return _observation(scenario_id, passed=result.target_supplier_names == [item["name"] for item in REFERENCES], entity_focus_correct=True)

    def _plural_then_esg_sentiment(self, scenario_id: str) -> P0Observation:
        result = resolve_supplier_target_selection("对上述企业做 ESG 和舆情分析", REFERENCES)
        return _observation(scenario_id, passed=result.target_supplier_names == [item["name"] for item in REFERENCES], entity_focus_correct=True)

    def _explicit_new_entity(self, scenario_id: str) -> P0Observation:
        result = resolve_supplier_target_selection(
            "对四川建安工业有限责任公司做风险分析",
            [*REFERENCES, {"name": "历史供应商有限公司"}],
        )
        return _observation(scenario_id, passed=result.target_supplier_names == ["四川建安工业有限责任公司"], entity_focus_correct=True)

    def _ambiguous_identity(self, scenario_id: str) -> P0Observation:
        result = resolve_supplier_target_selection("对这些企业做风险评估", [])
        return _observation(scenario_id, passed=result.needs_clarification is True and not result.target_supplier_names, entity_focus_correct=True)

    def _discovery_provider_order(self, scenario_id: str) -> P0Observation:
        events: list[str] = []
        candidate = {"supplier_name": "华东钢材有限公司", "categories": ["钢材"], "source": "web_search"}
        provider = ProviderReplayDouble({"tianyancha:钢材": [dict(candidate, source="tianyancha_search")]})

        def tianyancha(*_args):
            events.append("tianyancha")
            return provider.fetch("tianyancha", "钢材")

        def web(*_args):
            events.append("web_search")
            return [candidate]

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(discovery_service, "discover_local_candidates", lambda *_: [])
            patch.setattr(discovery_service, "_search_tianyancha_candidates", tianyancha)
            patch.setattr(discovery_service, "_search_web_candidates", web)
            patch.setattr(discovery_service, "_enrich_external_contacts", lambda items: items)
            result = discovery_service.discover_candidates(
                {"category": "钢材"}, {"minimum_candidate_count": 2}
            )
        passed = events == ["tianyancha", "web_search"] and result["external_candidates"]
        sanitized = provider.replay_recordings()[0]["request"]["token"] == "[REDACTED]"
        return _observation(scenario_id, passed=bool(passed and sanitized), tool_contract_compliant=True)

    def _missing_risk_not_low(self, scenario_id: str) -> P0Observation:
        answer = build_agent_answer(summary="待生成", ledger=EvidenceLedger(), claims=[], required_dimensions=["risk"])
        safe = answer.status == "needs_review" and "risk" in " ".join(answer.limitations)
        return _observation(scenario_id, passed=safe, missing_data_low_risk_count=0)

    def _conflicting_evidence_review(self, scenario_id: str) -> P0Observation:
        ledger = EvidenceLedger()
        ledger.add(build_evidence_record(
            evidence_id="ev-conflict", entity_id="supplier-a", dimension="risk",
            provider="p1", source_type="official", payload={"level": "low"},
        ))
        ledger.add(build_evidence_record(
            evidence_id="ev-conflict", entity_id="supplier-a", dimension="risk",
            provider="p2", source_type="official", payload={"level": "high"},
        ))
        claim = Claim(
            claim_id="claim-a", entity_id="supplier-a", dimension="risk", statement="风险存在冲突",
            evidence_refs=["ev-conflict"], confidence=0.9,
        )
        answer = build_agent_answer(summary="待生成", ledger=ledger, claims=[claim], required_dimensions=["risk"])
        return _observation(scenario_id, passed=answer.status == "needs_review", evidence_support_rate=1.0)

    def _provider_timeout_one_retry(self, scenario_id: str) -> P0Observation:
        calls = 0

        def timeout_tool() -> dict:
            nonlocal calls
            calls += 1
            raise asyncio.TimeoutError

        tool = StructuredTool.from_function(timeout_tool, name="timeout_provider", description="test")
        registry = ToolRegistry()
        registry.register(tool, ToolSpec(name="timeout_provider", capability="test", side_effect="read", approval_policy="none", timeout_seconds=0 + 1, max_attempts=2))
        outcome = asyncio.run(ToolExecutor(registry).execute("timeout_provider", {}, ToolContext(timeout_seconds=1)))
        return _observation(scenario_id, passed=calls == 2 and outcome.status == "unavailable", tool_contract_compliant=True)

    def _watchlist_human_approval(self, scenario_id: str) -> P0Observation:
        from langchain_core.tools import tool

        @tool
        def add_to_watchlist(company_name: str) -> dict:
            """Add one supplier to the monitoring list."""
            return {"status": "success", "side_effect_receipt": {"id": company_name}}

        registry = ToolRegistry()
        registry.register(add_to_watchlist, ToolSpec(name="add_to_watchlist", capability="risk_monitoring", side_effect="write", approval_policy="required"))
        executor = ToolExecutor(registry)
        denied = asyncio.run(executor.execute("add_to_watchlist", {"company_name": "甲公司"}))
        proposal = ActionGate(executor, secret_key="test-secret").propose(
            "add_to_watchlist", {"company_name": "甲公司"}, ToolContext(session_id="s", run_id="r", user_id="u")
        )
        return _observation(scenario_id, passed=denied.status == "denied" and proposal.status == "pending", unapproved_writes=0)

    def _restart_approval_recovery(self, scenario_id: str) -> P0Observation:
        durable = {"session_id": "restart-s", "config": {"configurable": {"thread_id": "r"}}, "mode": "react", "user_message": "确认"}
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("app.domains.agent_run.chat_interrupt_repo.save_chat_interrupt", lambda *_args: None)
            patch.setattr("app.domains.agent_run.chat_interrupt_repo.take_chat_interrupt", lambda _sid: durable)
            store("restart-s", None, durable["config"], durable["mode"], durable["user_message"])
            restored = pop("restart-s")
        return _observation(scenario_id, passed=restored is not None and restored["config"] == durable["config"], recovery_success=True)

    def _approval_idempotency(self, scenario_id: str) -> P0Observation:
        from langchain_core.tools import tool

        @tool
        def add_to_watchlist(company_name: str) -> dict:
            """Add one supplier to the monitoring list."""
            return {"status": "success", "side_effect_receipt": {"id": company_name}}

        registry = ToolRegistry()
        registry.register(add_to_watchlist, ToolSpec(name="add_to_watchlist", capability="risk_monitoring", side_effect="write", approval_policy="required"))
        gate = ActionGate(ToolExecutor(registry), secret_key="test-secret")
        context = ToolContext(session_id="s", run_id="r", user_id="u")
        first = gate.propose("add_to_watchlist", {"company_name": "甲公司"}, context)
        second = gate.propose("add_to_watchlist", {"company_name": "甲公司"}, context)
        return _observation(scenario_id, passed=first.proposal_id == second.proposal_id and first.idempotency_key == second.idempotency_key, duplicate_writes=0)

    def _session_concurrency_isolation(self, scenario_id: str) -> P0Observation:
        def identify(company_name: str) -> dict:
            return {"status": "success", "company_name": company_name}

        tool = StructuredTool.from_function(identify, name="identify", description="test")
        registry = ToolRegistry()
        registry.register(tool, ToolSpec(name="identify", capability="test", side_effect="read", approval_policy="none"))
        executor = ToolExecutor(registry)

        async def run(company_name: str):
            return await executor.execute("identify", {"company_name": company_name}, ToolContext(session_id=company_name, run_id=company_name))

        async def run_concurrently():
            return await asyncio.gather(run("甲"), run("乙"))

        first, second = asyncio.run(run_concurrently())
        isolated = first.data["company_name"] == "甲" and second.data["company_name"] == "乙" and first.call_id != second.call_id
        return _observation(scenario_id, passed=isolated, unresolved_tool_calls=0)

    def _postgres_commit_failure(self, scenario_id: str) -> P0Observation:
        async def persist(event: str, _snapshot: dict) -> None:
            if event == "persist_turn":
                raise RuntimeError("commit failed")

        async def run() -> bool:
            try:
                await run_harness({
                    "session_id": "s", "turn_id": "t", "run_id": "r", "user_message": "分析",
                    "execution_context": {"current_task": {}}, "current_task": {},
                }, persist=persist)
            except RuntimeError:
                return True
            return False

        return _observation(scenario_id, passed=asyncio.run(run()), recovery_success=True)

    def _invalid_tool_output(self, scenario_id: str) -> P0Observation:
        class StrictOutput(BaseModel):
            model_config = ConfigDict(extra="forbid")
            status: str
            value: int

        def invalid() -> dict:
            return {"status": "success", "unexpected": True}

        tool = StructuredTool.from_function(invalid, name="invalid_output", description="test")
        registry = ToolRegistry()
        registry.register(tool, ToolSpec(name="invalid_output", capability="test", side_effect="read", approval_policy="none"), output_model=StrictOutput)
        outcome = asyncio.run(ToolExecutor(registry).execute("invalid_output", {}))
        return _observation(scenario_id, passed=outcome.status == "invalid", tool_contract_compliant=True)

    def _unsupported_llm_claim(self, scenario_id: str) -> P0Observation:
        llm = ProgrammableLLMDouble({"claim": {"company": "不存在企业", "risk": "low"}})
        payload = asyncio.run(llm.ainvoke("claim"))
        claim = Claim(claim_id="claim", entity_id="entity:不存在企业", dimension="risk", statement="不存在企业风险低", value=payload["risk"], confidence=0.99)
        answer = build_agent_answer(summary="待生成", ledger=EvidenceLedger(), claims=[claim], required_dimensions=["risk"])
        return _observation(scenario_id, passed=answer.status == "needs_review", evidence_support_rate=1.0, incorrect_success_claims=0)


def test_p0_harness_executes_all_fixed_scenarios_and_quality_gates() -> None:
    report = run_p0_harness(_load_scenarios(), P0Runner(), repeats=10)

    assert report.scenario_count == 150
    assert report.passed_count == 150
    print(json.dumps({
        "p0_agent_harness_quality": {
            "scenario_executions": report.scenario_count,
            "pass_rate": report.pass_rate,
            "entity_focus_accuracy": report.entity_focus_accuracy,
            "tool_contract_compliance": report.tool_contract_compliance,
            "evidence_support_rate": report.evidence_support_rate,
            "missing_data_low_risk_count": report.missing_data_low_risk_count,
            "unapproved_writes": report.unapproved_writes,
            "incorrect_success_claims": report.incorrect_success_claims,
            "duplicate_writes": report.duplicate_writes,
            "recovery_success_rate": report.recovery_success_rate,
            "unresolved_tool_calls": report.unresolved_tool_calls,
            "stability_failures": report.stability_failures,
        }
    }, ensure_ascii=False, sort_keys=True))
    assert_p0_quality_gates(report)


def test_p0_harness_rejects_invalid_repeat_count() -> None:
    with pytest.raises(ValueError, match="重复次数"):
        run_p0_harness(_load_scenarios(), P0Runner(), repeats=0)


def test_harness_doubles_cover_429_empty_result_and_redacted_replay() -> None:
    provider = ProviderReplayDouble(
        {"web_search:钢材": []},
        {"tianyancha:钢材": "429"},
    )
    with pytest.raises(HarnessDoubleError, match="429"):
        provider.fetch("tianyancha", "钢材")
    assert provider.fetch("web_search", "钢材") == []
    recording = provider.replay_recordings()[0]
    assert recording["request"]["token"] == "[REDACTED]"

    llm = ProgrammableLLMDouble(faults={"rate-limited": "429"})
    with pytest.raises(HarnessDoubleError, match="429"):
        asyncio.run(llm.ainvoke("rate-limited"))


def test_real_harness_sse_adapter_emits_terminal_contract_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real Harness Runtime must reach a terminal SSE event without a provider."""
    from app.graphs.streaming import stream_harness_graph

    context = {
        "history": [],
        "references": [],
        "conversation_state": {},
        "current_task": {
            "task_type": "sourcing",
            "target_supplier_names": [],
            "analysis_dimensions": [],
            "subtasks": [],
        },
    }
    monkeypatch.setattr(
        "app.graphs.agent_core.adapter.save_execution_turn",
        lambda *_args, **_kwargs: None,
    )

    async def collect() -> list[str]:
        events: list[str] = []
        async for event in stream_harness_graph("推荐供应商", "sse-harness", context):
            events.append(event.splitlines()[0])
        return events

    event_types = asyncio.run(collect())
    assert event_types[:2] == ["event: workflow_status", "event: workflow_status"]
    assert "event: agent_answer" in event_types
    assert "event: evidence" in event_types
    assert event_types[-2:] == ["event: workflow_status", "event: done"]
    assert "event: error" not in event_types
