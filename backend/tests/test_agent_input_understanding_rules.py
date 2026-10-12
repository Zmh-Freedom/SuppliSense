"""Deterministic guardrails for frequently misread chat turns."""

from __future__ import annotations

from app.evals.agent_input_understanding import _evaluation_decision
from app.graphs.agent_core.adapter import _apply_harness_sourcing_requirement, _sourcing_constraint_slots
from app.graphs.agent_core.intent_extractor import _sourcing_specification_is_required
from app.graphs.agent_core.intent_extractor import _expand_generic_risk_dimensions
from app.graphs.agent_core.capability_routing import detect_explicit_capabilities, explicit_primary_capability


def test_sourcing_slots_clear_budget_and_capture_delivery_priority() -> None:
    slots = _sourcing_constraint_slots("预算先不考虑，优先交货周期。")

    assert slots["budget"] is None
    assert slots["delivery"] == "优先交货周期"


def test_sourcing_slots_keep_budget_tax_delivery_time_and_destination() -> None:
    slots = _sourcing_constraint_slots(
        "采购1000件不锈钢螺栓，含税预算不超过2万元，三个月内交付到苏州。"
    )

    assert slots["budget"] == "含税不超过2万元"
    assert slots["delivery"] == "三个月内"
    assert slots["supply_region"] == "苏州"


def test_harness_merges_explicit_single_turn_sourcing_slots(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing_risk.requirement_service.settings.LLM_API_KEY", "")
    context = {
        "references": [],
        "conversation_state": {},
        "current_task": {
            "task_type": "sourcing",
            "llm_sourcing_requirement": {
                "category": "紧固件/五金",
                "product": "不锈钢螺栓",
                "specification": "M8×30",
                "quantity": 1000,
                "budget": "不超过2万元(含税)",
                "delivery": "交付到苏州",
            },
        },
    }

    resolved = _apply_harness_sourcing_requirement(
        context,
        "采购1000件不锈钢螺栓，规格M8×30，含税预算不超过2万元，三个月内交付到苏州。",
    )

    requirement = resolved["current_task"]["requirement"]
    assert requirement["category"] == "螺栓"
    assert requirement["budget"] == "含税不超过2万元"
    assert requirement["delivery"] == "三个月内"
    assert requirement["supply_region"] == "苏州"


def test_complete_filters_release_specification_clarification(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing_risk.requirement_service.settings.LLM_API_KEY", "")
    context = {
        "references": [],
        "conversation_state": {},
        "llm_intent": {
            "task_type": "sourcing",
            "capability": "sourcing",
            "needs_clarification": True,
            "missing_fields": ["sourcing_requirement"],
            "clarification_question": "请补充具体技术参数。",
        },
        "current_task": {
            "task_type": "sourcing",
            "llm_sourcing_requirement": {
                "category": "工业相机",
                "product": "工业相机",
                "specification": "工业相机",
                "region": "华东",
                "budget": "50万元",
                "qualifications": "IATF 16949",
                "candidate_count": 5,
            },
        },
    }

    resolved = _apply_harness_sourcing_requirement(
        context,
        "找5家工业相机供应商，优先华东，预算50万元，要求支持IATF 16949。",
    )

    assert resolved["llm_intent"]["needs_clarification"] is False
    assert resolved["llm_intent"]["missing_fields"] == []
    assert resolved["llm_intent"]["clarification_question"] is None


def test_underdefined_sourcing_category_requires_spec_even_if_model_says_execute(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing_risk.requirement_service.settings.LLM_API_KEY", "")
    context = {
        "references": [],
        "conversation_state": {},
        "llm_intent": {
            "task_type": "sourcing",
            "capability": "sourcing",
            "needs_clarification": False,
            "missing_fields": [],
        },
        "current_task": {
            "task_type": "sourcing",
            "llm_sourcing_requirement": {
                "category": "蓄电池",
                "product": "蓄电池",
                "specification": "蓄电池",
                "optional_conditions": ["优先考虑价格"],
            },
        },
    }

    resolved = _apply_harness_sourcing_requirement(
        context,
        "找蓄电池供应商，优先考虑价格。",
    )

    assert resolved["llm_intent"]["needs_clarification"] is True
    assert resolved["llm_intent"]["missing_fields"] == ["sourcing_requirement"]


def test_price_preference_is_not_promoted_to_a_hard_sourcing_requirement(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing_risk.requirement_service.settings.LLM_API_KEY", "")
    context = {
        "references": [],
        "conversation_state": {},
        "current_task": {
            "task_type": "sourcing",
            "llm_sourcing_requirement": {
                "category": "电机",
                "product": "电机",
                "specification": "伺服电机",
                "must_have": ["价格优势", "支持连续运行"],
            },
        },
    }

    resolved = _apply_harness_sourcing_requirement(context, "找伺服电机，优先考虑价格。")

    requirement = resolved["current_task"]["requirement"]
    assert requirement["must_have"] == ["支持连续运行"]
    assert "优先考虑价格" in requirement["optional_conditions"]


def test_sourcing_specification_policy_clarifies_underdefined_new_requests() -> None:
    assert _sourcing_specification_is_required(
        "找蓄电池供应商，优先考虑价格。",
        {"category": "蓄电池", "specification": "蓄电池", "optional_conditions": ["优先考虑价格"]},
        {},
    ) is True
    assert _sourcing_specification_is_required(
        "给我推荐华南地区、具备IATF 16949资质的汽车线束厂家。",
        {
            "category": "汽车线束", "specification": "汽车线束",
            "region": "华南", "qualifications": "IATF 16949",
        },
        {},
    ) is True


def test_sourcing_specification_policy_accepts_complete_or_contextual_requests() -> None:
    assert _sourcing_specification_is_required(
        "找5家工业相机供应商，优先华东，预算50万元，要求支持IATF 16949。",
        {
            "category": "工业相机", "specification": "工业相机", "region": "华东",
            "budget": "50万元", "qualifications": "IATF 16949", "candidate_count": 5,
        },
        {},
    ) is False
    assert _sourcing_specification_is_required(
        "预算先不考虑，优先交货周期。",
        {"category": "工业相机", "specification": "工业相机"},
        {"current_requirement": {"category": "工业相机", "budget": "50万元"}},
    ) is False
    assert _sourcing_specification_is_required(
        "帮我找能供应制动系统的历史合作供应商。",
        {"category": "制动系统", "specification": "制动系统"},
        {},
    ) is False
    assert _sourcing_specification_is_required(
        "给我推荐华南地区、具备IATF 16949资质的汽车线束厂家。",
        {
            "category": "汽车线束", "specification": "汽车线束",
            "region": "华南", "supply_region": "华南地区", "qualifications": "IATF 16949",
        },
        {},
    ) is True


def test_explicit_compliance_and_composite_risk_capabilities_are_ordered() -> None:
    assert explicit_primary_capability("查一下苏州恒远电子有限公司有没有诉讼和行政处罚。") == "compliance"
    assert detect_explicit_capabilities("分析深圳云钥科技有限公司的经营和交付风险。") == [
        "business_risk", "delivery",
    ]


def test_comprehensive_risk_does_not_expand_into_unrequested_dimensions() -> None:
    assert _expand_generic_risk_dimensions(
        "请看青岛三祥科技股份有限公司的综合风险。",
        ["risk"],
    ) == ["risk"]
    assert _expand_generic_risk_dimensions("帮我分析一下供应商风险。", ["risk"]) == ["risk"]


def test_evaluation_reports_ambiguous_supplier_identity_before_generic_target_missing() -> None:
    decision = _evaluation_decision(
        "海拉呢？",
        {"requested_action": "none", "needs_clarification": True, "missing_fields": ["target_supplier_names"]},
        {},
        {"entity_resolution": {"status": "ambiguous"}},
    )

    assert decision == {"route": "clarify", "missing_fields": ["supplier_identity"]}


def test_evaluation_treats_generic_sourcing_category_as_missing_category() -> None:
    decision = _evaluation_decision(
        "找一家合适的供应商",
        {"task_type": "sourcing", "requested_action": "none", "needs_clarification": True,
         "missing_fields": ["sourcing_requirement"]},
        {"requirement": {"category": "一家合适"}},
        {},
    )

    assert decision == {"route": "clarify", "missing_fields": ["product_or_category"]}
