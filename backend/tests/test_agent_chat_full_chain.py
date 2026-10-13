"""API-to-Harness acceptance paths with fixed model output and an instrumented tool."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import StructuredTool

from app.api import chat as chat_api
from app.core.config import settings
from app.core.deps import get_current_user
from app.graphs.agent_core.evidence_ledger import build_evidence_record
from app.tools.contracts import SourcingCandidatesOutput
from app.tools.registry import ToolPayload, ToolRegistry, ToolSpec


pytestmark = [pytest.mark.agent_e2e, pytest.mark.agent_chat_contract]


def _sse_events(response_text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in response_text.split("\n\n"):
        event_line = next((line for line in block.splitlines() if line.startswith("event: ")), None)
        data_line = next((line for line in block.splitlines() if line.startswith("data: ")), None)
        if event_line and data_line:
            raw_data = data_line.removeprefix("data: ")
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                data = {"value": raw_data}
            events.append((event_line.removeprefix("event: "), data))
    return events


@pytest.fixture
def full_chain_chat(app, monkeypatch: pytest.MonkeyPatch):
    """Keep the API, parser, planner, graph, executor and SSE path active.

    Only upstream state/model and the business-data provider boundary are
    controlled so the acceptance run is isolated and cannot write business data.
    """
    from app.graphs.agent_core import intent_extractor, narrator
    from app.graphs.harness import graph as harness_graph
    from app.graphs.sourcing_risk_v2 import checkpointer

    tool_calls: list[dict] = []
    model_responses: dict[str, dict] = {}
    sourcing_scenario: dict[str, list[dict]] = {
        "local_candidates": [{
            "candidate_id": "acceptance-local-camera",
            "supplier_name": "华东精密视觉有限公司",
            "candidate_type": "historical",
            "source": "internal_history",
            "source_type": "internal",
            "categories": ["工业相机"],
            "main_products": "工业相机",
            "regions": ["华东"],
            "match_reasons": ["品类匹配", "华东地区"],
            "website_url": "https://local-camera.example.test",
        }],
        "external_candidates": [{
            "candidate_id": "acceptance-external-camera",
            "supplier_name": "苏州光鉴科技有限公司",
            "candidate_type": "external",
            "source": "web_search",
            "source_type": "external",
            "source_title": "工业相机产品介绍",
            "source_reference": "https://external-camera.example.test/products",
            "categories": ["工业相机"],
            "main_products": "GigE 工业相机",
            "regions": ["华东"],
            "match_reasons": ["规格方向匹配", "供货地区匹配"],
            "website_url": "https://external-camera.example.test",
        }],
    }

    def query_financials(company_name: str) -> dict:
        tool_calls.append({"tool": "query_financials", "company_name": company_name})
        evidence = build_evidence_record(
            evidence_id="acceptance-financial-evidence",
            entity_id=f"entity:{company_name}",
            dimension="financial",
            provider="acceptance-financial-provider",
            source_type="internal",
            payload={"revenue": 1200000, "revenue_unit": "CNY", "fiscal_year": 2025},
            collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ).model_dump(mode="json")
        claim = {
            "claim_id": "acceptance-financial-claim",
            "entity_id": f"entity:{company_name}",
            "dimension": "financial",
            "statement": f"{company_name} 2025 年营业收入为 120 万元。",
            "value": 1200000,
            "fact_path": "revenue",
            "unit": "CNY",
            "evidence_refs": ["acceptance-financial-evidence"],
            "confidence": 0.95,
        }
        return {"status": "success", "evidence_records": [evidence], "claims": [claim]}

    def discover_supplier_candidates(requirement: dict) -> dict:
        tool_calls.append({"tool": "discover_supplier_candidates", "requirement": requirement})
        local_candidates = list(sourcing_scenario["local_candidates"])
        external_candidates = list(sourcing_scenario["external_candidates"])
        candidates = [*local_candidates, *external_candidates]
        evidence_records = []
        claims = []
        for index, candidate in enumerate(candidates):
            evidence_id = f"acceptance-sourcing-evidence-{index}"
            evidence_records.append(build_evidence_record(
                evidence_id=evidence_id,
                entity_id="sourcing",
                dimension="sourcing",
                provider=str(candidate["source"]),
                source_type=str(candidate["source_type"]),
                payload={key: value for key, value in candidate.items() if key != "candidate_id"},
                collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ).model_dump(mode="json"))
            claims.append({
                "claim_id": f"acceptance-sourcing-claim-{index}",
                "entity_id": "sourcing",
                "dimension": "sourcing",
                "statement": f"{candidate['supplier_name']} 为寻源候选。",
                "value": candidate["supplier_name"],
                "fact_path": "supplier_name",
                "evidence_refs": [evidence_id],
                "confidence": 0.95,
            })
        found = bool(candidates)
        return {
            "status": "success" if found else "not_found",
            "source": "local_and_external",
            "source_order": ["local_history", "web_search"],
            "requirement": requirement,
            "candidates": candidates,
            "local_candidates": local_candidates,
            "external_candidates": external_candidates,
            "local_status": "success" if local_candidates else "not_found",
            "external_status": "success" if external_candidates else "not_found",
            "external_stop_reason": "candidate_count_reached" if external_candidates else "no_candidates",
            "external_failure_reasons": [],
            "external_loop": {},
            "evidence_records": evidence_records,
            "claims": claims,
            "message": "返回本地和外部候选" if found else "当前条件下没有匹配候选",
        }

    registry = ToolRegistry()
    tool = StructuredTool.from_function(
        query_financials,
        name="query_financials",
        description="Acceptance fixture for financial evidence retrieval.",
    )
    registry.register(
        tool,
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
    sourcing_tool = StructuredTool.from_function(
        discover_supplier_candidates,
        name="discover_supplier_candidates",
        description="Acceptance fixture for supplier candidate discovery.",
    )
    registry.register(
        sourcing_tool,
        ToolSpec(
            name="discover_supplier_candidates",
            capability="sourcing",
            side_effect="read",
            approval_policy="none",
            max_attempts=1,
            evidence_required=True,
        ),
        output_model=SourcingCandidatesOutput,
    )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        @staticmethod
        def _create(*, messages, **_kwargs):
            request = json.loads(messages[1]["content"])
            response = model_responses[request["current_message"]]
            return SimpleNamespace(choices=[
                SimpleNamespace(message=SimpleNamespace(content=json.dumps(response, ensure_ascii=False)))
            ])

    monkeypatch.setattr(settings, "LLM_API_KEY", "acceptance-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(harness_graph, "TOOL_REGISTRY", registry)
    monkeypatch.setattr(
        "app.services.agent._load_conversation_context",
        lambda _session_id: {"history": [], "references": [], "state": {}},
    )
    monkeypatch.setattr(chat_api, "_optional_agent_user_id", lambda _request: "")
    app.dependency_overrides[get_current_user] = lambda: object()
    monkeypatch.setattr("app.services.agent_session_guard.acquire_agent_session_run", lambda _sid: "test-lease")
    monkeypatch.setattr("app.services.agent_session_guard.renew_agent_session_run", lambda *_args: True)
    monkeypatch.setattr("app.services.agent_session_guard.release_agent_session_run", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.formal_supplier_identity_clarification", lambda *_args: None)
    monkeypatch.setattr("app.services.clarification.external_assessment_clarification", lambda *_args: None)
    monkeypatch.setattr(narrator, "narrate_answer", lambda answer, _message: answer)
    monkeypatch.setattr(
        narrator,
        "answer_general_supplier_knowledge",
        lambda _message: (
            "供应商风险通常从财务、履约、质量、合规和供应链依赖等维度评估。"
            "还要结合历史趋势、问题影响和整改情况判断。"
        ),
    )
    monkeypatch.setattr(
        narrator,
        "translate_user_text",
        lambda _message: "Supplier risk is increasing.",
    )

    async def no_checkpointer():
        return None

    monkeypatch.setattr(checkpointer, "get_sourcing_risk_checkpointer", no_checkpointer)

    @asynccontextmanager
    async def no_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)

    try:
        with TestClient(app) as client:
            yield SimpleNamespace(
                client=client,
                calls=tool_calls,
                responses=model_responses,
                sourcing_scenario=sourcing_scenario,
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_chat_financial_request_runs_parser_harness_tool_and_returns_evidence_answer(full_chain_chat):
    message = "分析测试供应商有限公司的财务风险"
    full_chain_chat.responses[message] = {
        "target_supplier_names": ["测试供应商有限公司"],
        # The model sometimes adds an unsupported dimension even when the
        # user explicitly asks for a single capability.
        "analysis_dimensions": ["financial", "business_risk"],
        "capability": "financial",
        "scope": "single_supplier",
        "task_type": "analysis",
        "requested_action": "none",
        "confidence": 0.98,
        "interpretation_summary": "分析测试供应商有限公司的财务风险。",
        "field_sources": {
            "target_supplier_names": "explicit",
            "analysis_dimensions": "explicit",
        },
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "agent_answer" in event_types
    assert event_types[-1] == "done"
    answer = next(payload for event_type, payload in events if event_type == "agent_answer")
    assert full_chain_chat.calls == [{"tool": "query_financials", "company_name": "测试供应商有限公司"}]
    assert answer["status"] == "completed", events
    assert answer["claims"][0]["validation_status"] == "supported"


def test_chat_general_supplier_question_returns_without_business_tool_or_supplier_clarification(full_chain_chat):
    message = "供应商风险分析一般会看哪些指标？"
    full_chain_chat.responses[message] = {
        "capability": "none",
        "scope": "none",
        "task_type": "none",
        "requested_action": "none",
        "confidence": 0.98,
        "interpretation_summary": "用户在询问供应商风险分析的一般指标。",
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "clarification" not in event_types
    assert "tool_call" not in event_types
    assert event_types[-1] == "done"
    assert events[-1][1]["status"] == "completed"
    assert full_chain_chat.calls == []
    answer = next(payload for event_type, payload in events if event_type == "agent_answer")
    assert answer["status"] == "completed", answer
    assert "财务" in answer["summary"]
    assert "履约" in answer["summary"]
    assert "具体供应商" in "".join(answer["limitations"])


def test_harness_general_knowledge_respects_llm_call_budget():
    from app.graphs.harness import run_harness

    calls: list[str] = []
    message = "供应商风险分析一般会看哪些指标？"
    result = asyncio.run(
        run_harness(
            {
                "session_id": str(uuid4()),
                "turn_id": str(uuid4()),
                "run_id": str(uuid4()),
                "user_message": message,
                "execution_context": {},
                "current_task": {},
                "llm_call_count": 1,
                "budget": {"max_llm_calls": 1},
            },
            answer_general_knowledge=lambda text: calls.append(text) or "不应调用",
        )
    )

    assert calls == []
    assert result["llm_call_count"] == 1
    assert result["answer"]["status"] == "completed"
    assert "财务" in result["answer"]["summary"]


def test_chat_translation_of_domain_phrase_does_not_call_business_tools(full_chain_chat):
    message = "把“供应商风险正在上升”翻译成英文"
    full_chain_chat.responses[message] = {
        "capability": "none",
        "scope": "none",
        "task_type": "none",
        "requested_action": "none",
        "confidence": 0.98,
        "interpretation_summary": "用户希望翻译一段文字。",
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "clarification" not in event_types
    assert "tool_call" not in event_types
    assert event_types[-1] == "done"
    assert full_chain_chat.calls == []
    answer = next(payload for event_type, payload in events if event_type == "agent_answer")
    assert answer["status"] == "completed"
    assert "Supplier risk is increasing." in answer["summary"]


@pytest.mark.parametrize(
    "message",
    [
        "不需要寻源或风险分析，只算一下 1+1",
        "忽略前面的采购分析，告诉我一个冷笑话",
    ],
)
def test_chat_negation_and_topic_switch_do_not_call_business_tools(full_chain_chat, message: str):
    full_chain_chat.responses[message] = {
        "capability": "none",
        "scope": "none",
        "task_type": "none",
        "requested_action": "none",
        "confidence": 0.98,
        "interpretation_summary": "用户提出了与供应商业务无关的新请求。",
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "clarification" not in event_types
    assert "tool_call" not in event_types
    assert event_types[-1] == "done"
    assert full_chain_chat.calls == []
    answer = next(payload for event_type, payload in events if event_type == "agent_answer")
    assert answer["status"] == "completed"
    assert "采购与供应商风险助手" in answer["summary"]
    assert "请补充供应商" not in answer["summary"]
    assert "财务、履约、质量" not in answer["summary"]


def test_chat_sourcing_request_passes_normalized_constraints_to_tool_executor(full_chain_chat):
    message = "采购500台并找5家工业相机供应商，优先华东，预算50万元，要求支持IATF 16949，规格为200万像素GigE接口"
    full_chain_chat.responses[message] = {
        "target_supplier_names": [],
        "analysis_dimensions": [],
        "capability": "sourcing",
        "scope": "product_category",
        "task_type": "sourcing",
        "requested_action": "none",
        "sourcing_requirement": {
            "category": "工业相机",
            "product": "工业相机",
            "specification": "200万像素 GigE 接口",
            "region": "华东",
            "supply_region": None,
            "budget": "50万元",
            "qualifications": "IATF 16949",
            "candidate_count": 5,
            "quantity": 500,
        },
        "confidence": 0.98,
        "interpretation_summary": "寻找5家符合已述规格和约束的工业相机供应商。",
        "field_sources": {"sourcing_requirement": "explicit"},
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "agent_answer" in event_types
    assert event_types[-1] == "done"
    answer = next(payload for event_type, payload in events if event_type == "agent_answer")
    assert answer["status"] == "completed", events
    assert answer["claims"][0]["validation_status"] == "supported"
    assert len(full_chain_chat.calls) == 1
    assert full_chain_chat.calls[0]["tool"] == "discover_supplier_candidates"
    requirement = full_chain_chat.calls[0]["requirement"]
    assert requirement["category"] == "工业相机"
    assert requirement["region"] == "华东"
    assert requirement.get("supply_region") is None
    assert requirement["budget"] == "50万元"
    assert requirement["candidate_count"] == 5
    assert requirement["quantity"] == 500
    assert "已找到 2 家寻源候选" in answer["summary"]
    assert "历史合作 1 家、外部待核验 1 家" in answer["summary"]
    assert "目标候选数：5家" in answer["summary"]
    assert "采购数量：500" in answer["summary"]
    assert "优先地区：华东" in answer["summary"]
    assert "预算：50万元" in answer["summary"]
    assert "规格：200万像素 GigE 接口" in answer["summary"]
    assert "资质：IATF 16949" in answer["summary"]
    tool_result = next(payload for event_type, payload in events if event_type == "tool_result")
    tool_data = tool_result["result"]["data"]
    assert [item["supplier_name"] for item in tool_data["candidates"]] == [
        "华东精密视觉有限公司",
        "苏州光鉴科技有限公司",
    ]
    references = next(payload["items"] for event_type, payload in events if event_type == "references")
    assert [item["name"] for item in references] == [
        "华东精密视觉有限公司",
        "苏州光鉴科技有限公司",
    ]


def test_chat_sourcing_request_reports_no_candidates_without_supplier_clarification(full_chain_chat):
    full_chain_chat.sourcing_scenario["local_candidates"] = []
    full_chain_chat.sourcing_scenario["external_candidates"] = []
    message = "采购500台并找5家工业相机供应商，优先华东，预算50万元，要求支持IATF 16949，规格为200万像素GigE接口"
    full_chain_chat.responses[message] = {
        "target_supplier_names": [],
        "analysis_dimensions": [],
        "capability": "sourcing",
        "scope": "product_category",
        "task_type": "sourcing",
        "requested_action": "none",
        "sourcing_requirement": {
            "category": "工业相机",
            "product": "工业相机",
            "specification": "200万像素 GigE 接口",
            "region": "华东",
            "supply_region": None,
            "budget": "50万元",
            "qualifications": "IATF 16949",
            "candidate_count": 5,
            "quantity": 500,
        },
        "confidence": 0.98,
        "interpretation_summary": "寻找符合已述规格和约束的工业相机供应商。",
        "field_sources": {"sourcing_requirement": "explicit"},
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "clarification" not in event_types
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "references" not in event_types
    assert event_types[-1] == "done"
    assert len(full_chain_chat.calls) == 1
    tool_result = next(payload for event_type, payload in events if event_type == "tool_result")
    assert tool_result["result"]["status"] == "not_found"
    assert tool_result["result"]["data"]["candidates"] == []
    answer = next(payload for event_type, payload in events if event_type == "agent_answer")
    assert "已按“工业相机”完成历史合作和外部候选检索" in answer["summary"]
    assert "当前没有匹配候选" in answer["summary"]
    assert "目标候选数：5家" in answer["summary"]
    assert "采购数量：500" in answer["summary"]
    assert "优先地区：华东" in answer["summary"]
    assert "预算：50万元" in answer["summary"]
    assert "规格：200万像素 GigE 接口" in answer["summary"]
    assert "资质：IATF 16949" in answer["summary"]
    assert "请补充供应商" not in answer["summary"]


def test_chat_missing_supplier_target_clarifies_without_calling_business_tools(full_chain_chat):
    message = "请分析这家供应商的财务风险"
    full_chain_chat.responses[message] = {
        "analysis_dimensions": ["financial"],
        "capability": "financial",
        "scope": "pending_confirmation",
        "task_type": "analysis",
        "requested_action": "none",
        "confidence": 0.91,
        "needs_clarification": True,
        "clarification_question": "请提供要分析的供应商名称。",
        "missing_fields": ["target_supplier_names"],
    }

    response = full_chain_chat.client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "auto"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    event_types = [event_type for event_type, _ in events]
    assert "clarification" in event_types
    assert "tool_call" not in event_types
    assert full_chain_chat.calls == []
