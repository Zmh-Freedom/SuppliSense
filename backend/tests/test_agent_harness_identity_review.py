from __future__ import annotations

import asyncio

from app.graphs.agent_core.adapter import _apply_identity_verification_intent
from app.graphs.agent_core.intent_extractor import (
    extract_monitor_target_id,
    is_identity_verification_request,
)
from app.graphs.harness.graph import _build_default_plan


MESSAGE = (
    "请核验监控对象“上海海拉电子有限公司”的主体身份，"
    "监控对象ID为 189a3b05-5666-495a-8aa9-00d0a8cc7d58。"
    "只使用有证据支持的数据，并明确本轮未覆盖范围。"
)


def test_identity_request_extracts_stable_monitor_target_id() -> None:
    assert is_identity_verification_request(MESSAGE) is True
    assert extract_monitor_target_id(MESSAGE) == "189a3b05-5666-495a-8aa9-00d0a8cc7d58"


def test_identity_request_builds_harness_task_without_risk_dimensions() -> None:
    context = {
        "current_task": {
            "task_id": "identity-turn",
            "target_supplier_names": ["上海海拉电子有限公司"],
            "user_message": MESSAGE,
        },
        "conversation_state": {},
        "llm_intent": {},
    }
    resolved = _apply_identity_verification_intent(context, MESSAGE)
    plan = _build_default_plan({
        "current_task": resolved["current_task"],
        "execution_context": resolved,
        "task_specs": [],
    })

    assert len(plan) == 2
    assert plan[0].tool_name == "resolve_monitor_identity"
    assert plan[0].arguments["monitor_target_id"] == "189a3b05-5666-495a-8aa9-00d0a8cc7d58"
    assert plan[0].dimension == "identity_review"
    assert plan[1].tool_name == "lookup_company_identity"
    assert plan[1].arguments == {"company_name": "上海海拉电子有限公司"}
    assert plan[1].depends_on == [plan[0].task_id]


def test_identity_tool_output_contract_is_registered() -> None:
    from app.tools import TOOL_REGISTRY

    definition = TOOL_REGISTRY.get("resolve_monitor_identity")
    assert definition is not None
    output = definition.output_model.model_validate({
        "monitor_target_id": "target-1",
        "query": "示例公司",
        "resolution": "pending_verification",
        "exact": None,
        "candidates": [],
        "evidence_records": [],
        "claims": [],
    })
    assert output.query == "示例公司"


def test_identity_tool_executor_keeps_external_candidate_evidence(monkeypatch) -> None:
    from app.tools import TOOL_REGISTRY
    from app.tools.executor import ToolExecutor

    monkeypatch.setattr(
        "app.domains.company.service.search_identity",
        lambda query, limit: {"resolution": "pending_verification", "exact": None, "candidates": []},
    )
    monkeypatch.setattr(
        "app.domains.alert.intake_service._load_external_profile",
        lambda query: ({
            "company_name": query,
            "unified_social_credit_code": "91450200MAA7L76A5R",
            "registration_number": "450205000188432",
            "registration_status": "存续",
            "legal_person": "廖鸿胡",
            "source_reference": f"tyc:{query}",
        }, {"status": "available"}),
    )

    outcome = asyncio.run(ToolExecutor(TOOL_REGISTRY).execute(
        "resolve_monitor_identity",
        {"company_name": "赛克瑞浦动力电池系统有限公司"},
    ))

    assert outcome.status == "success"
    assert outcome.data["resolution"] == "candidates"
    assert outcome.data["candidates"][0]["candidate_type"] == "external_identity"
    assert outcome.data["evidence_records"]
    assert outcome.data["claims"]


def test_identity_harness_keeps_claim_supported_by_tool_evidence(monkeypatch) -> None:
    from app.graphs.harness import run_harness
    from app.tools import TOOL_REGISTRY
    from app.tools.executor import ToolExecutor

    monkeypatch.setattr(
        "app.domains.company.service.search_identity",
        lambda query, limit: {"resolution": "pending_verification", "exact": None, "candidates": []},
    )
    monkeypatch.setattr(
        "app.domains.alert.intake_service._load_external_profile",
        lambda query: ({
            "company_name": query,
            "unified_social_credit_code": "91450200MAA7L76A5R",
            "registration_number": "450205000188432",
            "registration_status": "存续",
            "legal_person": "廖鸿胡",
            "source_reference": f"tyc:{query}",
        }, {"status": "available"}),
    )

    message = "对赛克瑞浦动力电池系统有限公司进行主体核验"
    result = asyncio.run(run_harness({
        "session_id": "identity-session",
        "turn_id": "identity-turn",
        "run_id": "identity-run",
        "user_message": message,
        "execution_context": {"references": []},
        "current_task": {
            "task_id": "identity-task",
            "target_supplier_names": ["赛克瑞浦动力电池系统有限公司"],
            "analysis_dimensions": ["identity_review"],
            "capability": "identity_review",
            "scope": "single_supplier",
            "task_type": "analysis",
            "identity_verification": True,
            "user_message": message,
        },
        "task_specs": [{
            "task_id": "identity-task:identity_review",
            "tool_name": "resolve_monitor_identity",
            "arguments": {"company_name": "赛克瑞浦动力电池系统有限公司"},
            "entity_id": "entity:赛克瑞浦动力电池系统有限公司",
            "dimension": "identity_review",
            "resource_key": "entity:赛克瑞浦动力电池系统有限公司",
            "required": True,
            "evidence_requirements": ["identity_review"],
        }],
        "budget": {},
    }, executor=ToolExecutor(TOOL_REGISTRY)))

    assert result["answer"]["claims"]
    assert result["answer"]["claims"][0]["validation_status"] == "supported"
    assert result["answer"]["evidence_refs"]


def test_approved_monitor_write_confirmation_includes_risk_baseline() -> None:
    from app.graphs.agent_supervisor.graph import _format_action_receipts

    text = _format_action_receipts([{
        "data": {
            "company_name": "上海海拉电子有限公司",
            "risk_baseline_status": "created",
            "risk_score": 18,
            "risk_level": "低风险",
        }
    }])

    assert "已将上海海拉电子有限公司加入风险监控清单" in text
    assert "风险基线：18/100，低风险" in text


def test_explicit_provider_capability_adds_controlled_legal_lookup() -> None:
    plan = _build_default_plan({
        "current_task": {
            "task_id": "legal-turn",
            "target_supplier_names": ["示例汽车零部件有限公司"],
            "user_message": "查询示例汽车零部件有限公司的司法诉讼和被执行信息",
            "task_type": "analysis",
            "provider_capabilities": ["legal_risk"],
        },
        "execution_context": {},
        "task_specs": [],
    })

    legal_tasks = [task for task in plan if task.tool_name == "lookup_legal_risk"]
    assert len(legal_tasks) == 1
    assert legal_tasks[0].arguments == {"company_name": "示例汽车零部件有限公司"}
    assert legal_tasks[0].dimension == "legal_risk"
