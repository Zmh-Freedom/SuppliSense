"""Regression coverage for conversation states without an executable task."""

import pytest

from app.graphs.agent_core.adapter import apply_extracted_conversation_intent, build_execution_context
from app.graphs.agent_core.contracts import migrate_conversation_state
from app.graphs.agent_core.intent_extractor import ConversationIntentExtraction


@pytest.mark.parametrize(("follow_up", "expected_type", "expected_targets"), [
    ("我想查询青岛三祥的资料", "none", []),
    ("查看青岛三祥科技股份有限公司的风险", "analysis", ["青岛三祥科技股份有限公司"]),
    ("谢谢", "none", []),
])
def test_greeting_then_next_turn_can_restore_conversation(follow_up, expected_type, expected_targets):
    first = build_execution_context(session_id="test-session", user_message="你好")
    second = build_execution_context(
        session_id="test-session", user_message=follow_up,
        previous_state=first["conversation_state"],
    )
    assert first["conversation_state"]["current_task"] is None
    assert second["session_id"] == "test-session"
    assert second["current_task"]["user_message"] == follow_up
    assert second["current_task"]["task_type"] == expected_type
    assert second["current_task"]["target_supplier_names"] == expected_targets


def test_explicit_new_supplier_does_not_reuse_previous_task_target():
    first = build_execution_context(
        session_id="topic-switch-session",
        user_message="查看甲电机有限公司的风险",
    )
    second = build_execution_context(
        session_id="topic-switch-session",
        user_message="改问乙科技股份有限公司的风险",
        previous_state=first["conversation_state"],
    )

    assert second["current_task"]["target_supplier_names"] == ["乙科技股份有限公司"]
    assert second["current_task"]["user_message"] == "改问乙科技股份有限公司的风险"


@pytest.mark.parametrize("message", [
    "分析一下财务风险",
    "分析财务风险",
    "查看财务风险",
])
def test_analysis_dimension_words_are_not_supplier_names(message):
    context = build_execution_context(session_id="dimension-only", user_message=message)

    assert context["current_task"]["target_supplier_names"] == []
    assert context["conversation_state"]["selected_supplier_names"] == []


def test_analysis_dimension_words_do_not_replace_unrelated_supplier_references():
    context = build_execution_context(
        session_id="dimension-only-with-reference",
        user_message="分析一下财务风险",
        references=[{"kind": "supplier", "name": "甲供应商有限公司"}],
    )

    assert context["current_task"]["target_supplier_names"] == []
    assert context["conversation_state"]["selected_supplier_names"] == []


def test_dimension_word_can_still_identify_a_known_supplier_alias():
    context = build_execution_context(
        session_id="known-dimension-alias",
        user_message="分析财务这家供应商的风险",
        references=[{
            "kind": "supplier",
            "name": "财务科技有限公司",
            "supplier_id": "known-supplier-id",
            "aliases": ["财务"],
        }],
    )

    assert context["current_task"]["target_supplier_names"] == ["财务科技有限公司"]
    assert context["conversation_state"]["selected_supplier_names"] == ["财务科技有限公司"]


def test_load_context_still_clarifies_dimension_only_request_without_intent_extraction(monkeypatch):
    from app.graphs.agent_core import adapter, intent_extractor

    monkeypatch.setattr(intent_extractor, "extract_conversation_intent", lambda *_args, **_kwargs: None)

    context = adapter.load_execution_context(
        "dimension-only-no-intent",
        "分析一下财务风险",
    )

    assert context["current_task"]["target_supplier_names"] == []
    assert context["llm_intent"]["needs_clarification"] is True
    assert context["llm_intent"]["missing_fields"] == ["target_supplier_names"]
    assert context["conversation_state"]["input_understanding"]["missing_fields"] == [
        "target_supplier_names"
    ]


def test_no_intent_fallback_preserves_confirmed_pronoun_target():
    from app.graphs.agent_core.adapter import _clarify_dimension_only_intent

    first = build_execution_context(
        session_id="no-intent-pronoun",
        user_message="查看甲供应商有限公司的财务风险",
    )
    user_message = "它的财务风险怎么样？"
    context = build_execution_context(
        session_id="no-intent-pronoun",
        user_message=user_message,
        previous_state=first["conversation_state"],
    )

    assert context["current_task"]["target_supplier_names"] == ["甲供应商有限公司"]
    assert _clarify_dimension_only_intent(user_message, context, None) is None


def test_no_intent_fallback_preserves_explicit_new_supplier_target():
    from app.graphs.agent_core.adapter import _clarify_dimension_only_intent

    user_message = "分析乙供应商有限公司的财务风险"
    context = build_execution_context(session_id="no-intent-new-target", user_message=user_message)

    assert context["current_task"]["target_supplier_names"] == ["乙供应商有限公司"]
    assert _clarify_dimension_only_intent(user_message, context, None) is None


def test_no_intent_fallback_preserves_known_supplier_dimension_alias():
    from app.graphs.agent_core.adapter import _clarify_dimension_only_intent

    user_message = "分析财务这家供应商的风险"
    context = build_execution_context(
        session_id="no-intent-known-alias",
        user_message=user_message,
        references=[{
            "kind": "supplier",
            "name": "财务科技有限公司",
            "supplier_id": "known-supplier-id",
            "aliases": ["财务"],
        }],
    )

    assert context["current_task"]["target_supplier_names"] == ["财务科技有限公司"]
    assert _clarify_dimension_only_intent(user_message, context, None) is None


@pytest.mark.parametrize("old_task", [
    None,
    {},
    {"task_type": "none", "target_supplier_names": [], "analysis_dimensions": []},
    {"task_type": "none", "capability": "none", "scope": "none", "requested_action": "none"},
])
def test_old_empty_task_is_restored_as_no_task(old_task):
    state = migrate_conversation_state({"current_task": old_task, "pending_approvals": ["approval-1"]})
    assert state.current_task is None
    assert state.pending_approvals == ["approval-1"]


def test_none_with_business_requirement_is_not_silently_discarded():
    with pytest.raises(ValueError, match="none"):
        migrate_conversation_state({"current_task": {"task_type": "none", "requirement": {"category": "电机"}}})


@pytest.mark.parametrize("business_field,value", [
    ("capability", "financial"),
    ("scope", "single_supplier"),
    ("requested_action", "add_watchlist"),
    ("pending_clarification", {"missing_fields": ["target_supplier_names"]}),
])
def test_none_with_legacy_intent_fields_is_not_silently_discarded(business_field, value):
    with pytest.raises(ValueError, match="none"):
        migrate_conversation_state({
            "current_task": {"task_type": "none", business_field: value},
        })


def test_generic_turn_keeps_pending_clarification_and_requirement_when_task_is_null():
    pending = {
        "task_type": "analysis",
        "capability": "financial",
        "scope": "single_supplier",
        "target_supplier_names": [],
        "analysis_dimensions": ["financial"],
        "missing_fields": ["target_supplier_names"],
    }
    requirement = {"category": "工业相机"}
    previous_state = {
        "current_task": None,
        "current_requirement": requirement,
        "pending_clarification": pending,
        "pending_approvals": ["approval-1"],
    }
    context = build_execution_context(
        session_id="pending-null-task",
        user_message="你好",
        previous_state=previous_state,
    )
    resolved = apply_extracted_conversation_intent(
        context,
        ConversationIntentExtraction(task_type="none", capability="none"),
    )

    state = resolved["conversation_state"]
    assert resolved["current_task"]["user_message"] == "你好"
    assert state["current_task"] is None
    assert state["pending_clarification"] == pending
    assert state["current_requirement"] == requirement
    assert state["pending_approvals"] == ["approval-1"]


def test_pending_clarification_can_restore_task_when_previous_task_is_null():
    message = "上海凯众材料科技股份有限公司"
    pending = {
        "task_type": "analysis",
        "capability": "financial",
        "scope": "single_supplier",
        "target_supplier_names": [],
        "analysis_dimensions": ["financial"],
        "provider_capabilities": ["legal_risk"],
        "missing_fields": ["target_supplier_names"],
    }
    context = build_execution_context(
        session_id="pending-restore-null-task",
        user_message=message,
        previous_state={"current_task": None, "pending_clarification": pending},
    )
    resolved = apply_extracted_conversation_intent(context, None)

    assert resolved["current_task"]["user_message"] == message
    assert resolved["current_task"]["task_type"] == "analysis"
    assert resolved["current_task"]["analysis_dimensions"] == ["financial"]
    assert resolved["current_task"]["provider_capabilities"] == ["legal_risk"]
    assert resolved["current_task"]["capability"] == "financial"
    assert resolved["current_task"]["target_supplier_names"] == [message]
    assert "pending_clarification" not in resolved["conversation_state"]
