from __future__ import annotations

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

    assert len(plan) == 1
    assert plan[0].tool_name == "resolve_monitor_identity"
    assert plan[0].arguments["monitor_target_id"] == "189a3b05-5666-495a-8aa9-00d0a8cc7d58"
    assert plan[0].dimension == "identity_review"


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
