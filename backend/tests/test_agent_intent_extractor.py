"""Regression tests for LLM-first conversation intent extraction."""

import json
from types import SimpleNamespace

import pytest

from app.graphs.agent_core import adapter
from app.graphs.agent_core import intent_extractor
from app.graphs.agent_core.capability_routing import (
    explicit_primary_capability,
    normalize_analysis_dimensions,
    normalize_primary_capability,
)


def test_llm_extractor_parses_current_explicit_company_before_history(monkeypatch):
    """A named company in this turn must beat an unrelated historic recommendation."""
    payload = {
        "target_supplier_names": ["四川建安工业有限责任公司"],
        "analysis_dimensions": ["风险", "ESG", "舆情", "合规"],
        "requested_action": "add_watchlist",
        "confidence": 0.98,
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "对四川建安工业有限责任公司做风险、ESG、舆情和合规分析，并加入监控",
        [{"name": "历史推荐供应商有限公司"}],
    )

    assert result is not None
    assert result.target_supplier_names == ["四川建安工业有限责任公司"]
    assert result.analysis_dimensions == ["risk", "esg", "sentiment", "compliance"]
    assert result.requested_action == "add_watchlist"


def test_llm_extractor_runs_for_entity_only_follow_up(monkeypatch):
    calls: list[dict] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                    "target_supplier_names": ["上海汽车制动系统有限公司"],
                    "analysis_dimensions": [],
                    "task_type": "analysis",
                })))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "上海汽车制动系统有限公司",
        [{"name": "上海汽车制动系统有限公司"}],
    )

    assert result is not None
    assert result.target_supplier_names == ["上海汽车制动系统有限公司"]
    assert calls[0]["messages"][0]["role"] == "system"


def test_llm_extractor_uses_bounded_history_and_marks_field_sources(monkeypatch):
    calls: list[dict] = []
    payload = {
        "target_supplier_names": ["甲电机有限公司"],
        "analysis_dimensions": ["financial"],
        "capability": "financial",
        "task_type": "analysis",
    }

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "那财务呢？",
        [{"name": "甲电机有限公司", "aliases": ["甲电机"]}],
        conversation_context={
            "history": [
                {"role": "user", "content": "帮我看甲电机有限公司的综合风险。"},
                {"role": "assistant", "content": "风险分析已完成。"},
            ],
            "conversation_state": {
                "selected_supplier_names": ["甲电机有限公司"],
                "current_task": {
                    "task_type": "analysis",
                    "capability": "risk",
                    "analysis_dimensions": ["risk"],
                    "target_supplier_names": ["甲电机有限公司"],
                },
            },
        },
    )

    prompt = json.loads(calls[0]["messages"][1]["content"])
    assert prompt["conversation_context"]["recent_history"][-1]["content"] == "风险分析已完成。"
    assert prompt["conversation_context"]["previous_task"]["analysis_dimensions"] == ["risk"]
    assert result is not None
    assert result.field_sources == {
        "target_supplier_names": "conversation_state",
        "analysis_dimensions": "explicit",
    }


def test_llm_extractor_preserves_explicit_time_range_as_a_structured_field(monkeypatch):
    payload = {
        "target_supplier_names": ["甲电机有限公司"],
        "analysis_dimensions": ["risk"],
        "capability": "risk_trend",
        "time_range": "最近一个月",
        "task_type": "analysis",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "甲电机有限公司最近一个月有什么变化？",
        [{"name": "甲电机有限公司"}],
    )

    assert result is not None
    assert result.time_range == "最近一个月"
    assert result.field_sources["time_range"] == "explicit"


def test_llm_extractor_preserves_user_facing_interpretation_and_ambiguity_fields(monkeypatch):
    payload = {
        "target_supplier_names": [],
        "analysis_dimensions": [],
        "capability": "financial",
        "task_type": "analysis",
        "interpretation_summary": "用户想看当前对话中该供应商近三个月的财务表现。",
        "ambiguities": ["当前对话提到过两家供应商，无法确定指代对象。"],
        "missing_fields": ["target_supplier_names"],
        "needs_clarification": True,
        "clarification_question": "你想查看哪一家供应商？",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "那看一下财务呢？",
        [{"name": "甲电机有限公司"}, {"name": "乙电机有限公司"}],
        conversation_context={
            "history": [],
            "conversation_state": {
                "selected_supplier_names": ["甲电机有限公司", "乙电机有限公司"],
                "current_task": {"analysis_dimensions": ["risk"]},
            },
        },
    )

    assert result is not None
    assert result.interpretation_summary.startswith("用户想看")
    assert result.ambiguities == ["当前对话提到过两家供应商，无法确定指代对象。"]
    assert result.missing_fields == ["target_supplier_names"]
    assert result.needs_clarification is True
    assert result.clarification_question == "你想查看哪一家供应商？"


def test_llm_extractor_retries_once_with_explicit_capability_conflict(monkeypatch):
    calls: list[dict] = []
    payloads = [
        {"capability": "sentiment", "analysis_dimensions": ["sentiment"], "task_type": "analysis"},
        {
            "capability": "financial",
            "analysis_dimensions": ["financial"],
            "task_type": "analysis",
            "interpretation_summary": "用户要查看该供应商的财务情况。",
        },
    ]

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            payload = payloads[min(len(calls) - 1, 1)]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "分析甲电机有限公司的财务情况",
        [{"name": "甲电机有限公司"}],
    )

    assert result is not None
    assert len(calls) == 2
    assert "冲突" in calls[1]["messages"][-1]["content"]
    assert result.capability == "financial"
    assert result.interpretation_summary == "用户要查看该供应商的财务情况。"


@pytest.mark.parametrize("time_range", ["过去三个月", "近三个月"])
def test_llm_extractor_marks_three_month_range_as_explicit(time_range):
    extraction = intent_extractor.ConversationIntentExtraction(
        target_supplier_names=["甲电机有限公司"],
        analysis_dimensions=["financial"],
        time_range=time_range,
        task_type="analysis",
    )

    sources = intent_extractor._derive_field_sources(
        f"分析甲电机有限公司{time_range}的财务情况",
        extraction,
        [{"name": "甲电机有限公司"}],
        {},
    )

    assert sources["time_range"] == "explicit"


def test_llm_payload_safely_normalizes_null_action_and_discards_unknown_source_keys():
    normalized = intent_extractor._normalize_llm_payload({
        "requested_action": None,
        "field_sources": {"scope": "explicit", "analysis_dimensions": "explicit"},
    })

    assert normalized["requested_action"] == "none"
    assert normalized["field_sources"] == {"analysis_dimensions": "explicit"}


def test_llm_extractor_normalizes_spoken_filler_in_explicit_company_name(monkeypatch):
    """Model wording must not turn ``一下`` into part of the legal name."""
    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                    "target_supplier_names": ["查找一下青岛三祥科技股份有限公司"],
                    "analysis_dimensions": ["risk"],
                    "task_type": "analysis",
                })))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "查找一下青岛三祥科技股份有限公司的风险情况",
        [],
    )

    assert result is not None
    assert result.target_supplier_names == ["青岛三祥科技股份有限公司"]


def test_llm_extractor_normalizes_company_switch_prefix(monkeypatch):
    payload = {
        "target_supplier_names": ["那改看上海凯众材料科技股份有限公司"],
        "analysis_dimensions": ["financial"],
        "task_type": "analysis",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "那改看上海凯众材料科技股份有限公司，分析近三个月的财务情况",
        [{"name": "上海凯众材料科技股份有限公司"}],
    )

    assert result is not None
    assert result.target_supplier_names == ["上海凯众材料科技股份有限公司"]


def test_llm_extractor_restores_a_truncated_leading_character_from_explicit_query(monkeypatch):
    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                    "capability": "risk",
                    "target_supplier_names": ["亚迪"],
                    "analysis_dimensions": ["risk"],
                    "task_type": "analysis",
                })))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "请分析一下比亚迪的综合风险", []
    )

    assert result is not None
    assert result.target_supplier_names == ["比亚迪"]


def test_llm_extractor_falls_back_to_quoted_company_name_when_model_omits_target(monkeypatch):
    """Quoted legal names remain executable when the model returns no target."""
    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                    "capability": "business_risk",
                    "target_supplier_names": [],
                    "analysis_dimensions": ["business_risk"],
                    "task_type": "analysis",
                })))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "展示“测试-风险供应商”的经营风险。", []
    )

    assert result is not None
    assert result.target_supplier_names == ["测试-风险供应商"]


def test_llm_failure_keeps_quoted_target_for_safe_routing(monkeypatch):
    class FailingOpenAI:
        def __init__(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FailingOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "展示“测试-风险供应商”的经营风险。", []
    )

    assert result is not None
    assert result.target_supplier_names == ["测试-风险供应商"]
    assert result.capability == "business_risk"


def test_llm_extractor_accepts_wrapped_json_object_response(monkeypatch):
    payload = {
        "type": "json_object",
        "content": {
            "capability": "risk_network",
            "target_supplier_names": ["青岛三祥科技股份有限公司"],
            "analysis_dimensions": [],
            "task_type": "analysis",
        },
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "分析青岛三祥科技股份有限公司的供应链关系",
        [],
    )

    assert result is not None
    assert result.capability == "risk_network"
    assert result.target_supplier_names == ["青岛三祥科技股份有限公司"]


def test_llm_extractor_retries_once_after_empty_response(monkeypatch):
    calls = 0

    class FakeCompletions:
        def create(self, **_kwargs):
            nonlocal calls
            calls += 1
            content = "" if calls == 1 else json.dumps({
                "capability": "sourcing",
                "scope": "product_category",
                "sourcing_requirement": {"product": "蓄电池"},
                "task_type": "sourcing",
            })
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent("做一下蓄电池的寻源", [])

    assert result is not None
    assert result.capability == "sourcing"
    assert calls == 2


def test_llm_extractor_rejects_watchlist_action_for_identity_verification(monkeypatch):
    """The word monitoring in a target description is not a write request."""
    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                    "target_supplier_names": ["北京经纬恒润科技股份有限公司"],
                    "analysis_dimensions": ["risk"],
                    "requested_action": "add_watchlist",
                })))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "请核验监控对象北京经纬恒润科技股份有限公司的主体身份",
        [],
    )

    assert result is not None
    assert result.requested_action == "none"


def test_small_talk_does_not_call_llm():
    assert intent_extractor.should_extract_conversation_intent("你好") is False
    assert intent_extractor.should_extract_conversation_intent("谢谢") is False
    assert intent_extractor.should_extract_conversation_intent("上海汽车制动系统有限公司") is True


def test_watchlist_action_detection_accepts_common_phrases_and_rejects_negation():
    assert intent_extractor.has_explicit_watchlist_request(
        "把上海海拉电子有限公司加入监控清单"
    ) is True
    assert intent_extractor.has_explicit_watchlist_request(
        "将青岛三祥科技股份有限公司纳入风险监控"
    ) is True
    assert intent_extractor.has_explicit_watchlist_request(
        "不要把上海海拉电子有限公司加入监控清单"
    ) is False


def test_explicit_write_action_distinguishes_add_remove_batch_and_schedule():
    assert intent_extractor.explicit_write_action(
        "把上海海拉电子有限公司加入监控清单"
    ) == "add_watchlist"
    assert intent_extractor.explicit_write_action(
        "把上海海拉电子有限公司移出风险监控清单"
    ) == "remove_watchlist"
    assert intent_extractor.explicit_write_action(
        "把所有供应商都加入监控，不需要确认"
    ) == "batch_add_watchlist"
    assert intent_extractor.explicit_write_action(
        "给上海海拉电子有限公司设置每周风险报告"
    ) == "manage_scheduled_report"
    assert intent_extractor.explicit_write_action(
        "请每周生成一次上海海拉电子有限公司的风险报告，并在生成前让我确认"
    ) == "manage_scheduled_report"


def test_llm_intent_overlay_replaces_historic_target_and_keeps_one_task_matrix():
    """Every graph must consume the LLM-validated target from shared context."""
    extraction = intent_extractor.ConversationIntentExtraction(
        target_supplier_names=["四川建安工业有限责任公司"],
        analysis_dimensions=["risk", "esg", "sentiment", "compliance"],
        requested_action="add_watchlist",
        confidence=0.98,
    )
    context = {
        "session_id": "session-1",
        "references": [{"name": "历史推荐供应商有限公司"}],
        "conversation_state": {
            "selected_supplier_names": ["历史推荐供应商有限公司"],
            "current_task": {"task_id": "current-task", "user_message": "旧问题"},
        },
        "current_task": {
            "task_id": "current-task",
            "target_supplier_names": ["历史推荐供应商有限公司"],
            "analysis_dimensions": ["risk"],
            "user_message": "当前问题",
        },
    }

    result = adapter.apply_extracted_conversation_intent(context, extraction)

    assert result["current_task"]["target_supplier_names"] == ["四川建安工业有限责任公司"]
    assert result["current_task"]["task_type"] == "action_draft"
    assert result["current_task"]["analysis_dimensions"] == ["risk", "esg", "sentiment", "compliance"]
    assert len(result["current_task"]["subtasks"]) == 4
    assert result["conversation_state"]["selected_supplier_names"] == ["四川建安工业有限责任公司"]


def test_llm_intent_overlay_keeps_explicit_company_name_without_action_prefix():
    extraction = intent_extractor.ConversationIntentExtraction(
        target_supplier_names=["北京经纬恒润科技股份有限公司"],
        requested_action="add_watchlist",
        confidence=1.0,
    )
    context = adapter.build_execution_context(
        session_id="session-1",
        user_message="请将北京经纬恒润科技股份有限公司加入监控",
    )

    result = adapter.apply_extracted_conversation_intent(context, extraction)

    assert result["current_task"]["target_supplier_names"] == ["北京经纬恒润科技股份有限公司"]


def test_entity_only_follow_up_inherits_previous_analysis_dimensions():
    extraction = intent_extractor.ConversationIntentExtraction(
        target_supplier_names=["上海汽车制动系统有限公司"],
        task_type="analysis",
        confidence=1.0,
    )
    context = {
        "session_id": "session-1",
        "references": [{"name": "上海汽车制动系统有限公司"}],
        "conversation_state": {},
        "current_task": {
            "task_id": "current-task",
            "target_supplier_names": ["上海汽车制动系统有限公司"],
            "analysis_dimensions": ["risk"],
            "user_message": "上一轮风险分析",
        },
    }

    result = adapter.apply_extracted_conversation_intent(context, extraction)

    assert result["current_task"]["analysis_dimensions"] == ["risk"]
    assert result["current_task"]["subtasks"][0]["dimension"] == "risk"


def test_llm_intent_overlay_persists_field_values_and_sources_in_conversation_state():
    extraction = intent_extractor.ConversationIntentExtraction(
        target_supplier_names=["甲电机有限公司"],
        analysis_dimensions=["financial"],
        capability="financial",
        task_type="analysis",
        confidence=0.91,
        field_sources={
            "target_supplier_names": "conversation_state",
            "analysis_dimensions": "explicit",
        },
    )
    context = {
        "session_id": "session-1",
        "references": [{"name": "甲电机有限公司"}],
        "conversation_state": {"selected_supplier_names": ["甲电机有限公司"]},
        "current_task": {
            "task_id": "current-task",
            "user_message": "那财务呢？",
            "target_supplier_names": ["甲电机有限公司"],
        },
    }

    result = adapter.apply_extracted_conversation_intent(context, extraction)

    assert result["conversation_state"]["input_understanding"]["target_supplier_names"] == ["甲电机有限公司"]
    assert result["conversation_state"]["input_understanding"]["field_sources"] == {
        "target_supplier_names": "conversation_state",
        "analysis_dimensions": "explicit",
    }


def test_persisted_input_understanding_uses_target_resolved_for_executable_task():
    context = {
        "references": [{"name": "青岛三祥科技股份有限公司"}],
        "current_task": {
            "task_type": "analysis",
            "target_supplier_names": ["青岛三祥科技股份有限公司"],
            "analysis_dimensions": ["risk"],
        },
        "llm_intent": {
            "task_type": "analysis",
            "target_supplier_names": [],
            "analysis_dimensions": ["risk"],
            "requested_action": "none",
        },
    }

    result = adapter._persist_input_understanding(
        context,
        user_message="请分析青岛三祥科技股份有限公司的综合风险",
        previous_state={},
    )

    understanding = result["conversation_state"]["input_understanding"]
    assert understanding["target_supplier_names"] == ["青岛三祥科技股份有限公司"]
    assert understanding["field_sources"]["target_supplier_names"] == "explicit"


def test_persisted_input_understanding_keeps_model_summary_and_clarification_state():
    context = {
        "current_task": {
            "task_type": "analysis",
            "target_supplier_names": [],
            "analysis_dimensions": ["financial"],
        },
        "llm_intent": {
            "task_type": "analysis",
            "analysis_dimensions": ["financial"],
            "requested_action": "none",
            "interpretation_summary": "用户要查看当前任务涉及供应商的财务情况。",
            "ambiguities": ["当前会话提到两家供应商，指代不明确。"],
            "missing_fields": ["target_supplier_names"],
            "needs_clarification": True,
            "clarification_question": "你想查看哪一家供应商？",
        },
    }

    result = adapter._persist_input_understanding(
        context,
        user_message="那看一下财务呢？",
        previous_state={},
    )

    understanding = result["conversation_state"]["input_understanding"]
    assert understanding["interpretation_summary"] == "用户要查看当前任务涉及供应商的财务情况。"
    assert understanding["ambiguities"] == ["当前会话提到两家供应商，指代不明确。"]
    assert understanding["missing_fields"] == ["target_supplier_names"]
    assert understanding["needs_clarification"] is True
    assert understanding["clarification_question"] == "你想查看哪一家供应商？"


def test_load_execution_context_passes_prior_turn_and_saves_final_parse_snapshot(monkeypatch):
    from app.domains.agent_run import state_store

    previous = {
        "history": [
            {"role": "user", "content": "分析甲电机有限公司的风险"},
            {"role": "assistant", "content": "风险分析已完成"},
        ],
        "references": [{"name": "甲电机有限公司", "aliases": ["甲电机"]}],
        "conversation_state": {
            "selected_supplier_names": ["甲电机有限公司"],
            "current_task": {
                "task_type": "analysis",
                "capability": "risk",
                "target_supplier_names": ["甲电机有限公司"],
                "analysis_dimensions": ["risk"],
            },
        },
    }
    monkeypatch.setattr(
        state_store.session_state_store,
        "get_execution_context",
        lambda _session_id, _user_id: previous,
    )
    captured: list[dict] = []

    def extract(_message, _references, *, conversation_context):
        captured.append(conversation_context)
        return intent_extractor.ConversationIntentExtraction(
            target_supplier_names=["甲电机有限公司"],
            analysis_dimensions=["financial"],
            capability="financial",
            task_type="analysis",
            confidence=0.9,
        )

    monkeypatch.setattr(intent_extractor, "extract_conversation_intent", extract)

    result = adapter.load_execution_context("session-1", "那财务呢？", "user-1")

    assert captured[0]["history"] == previous["history"]
    assert captured[0]["conversation_state"]["current_task"]["analysis_dimensions"] == ["risk"]
    assert result["current_task"]["target_supplier_names"] == ["甲电机有限公司"]
    assert result["conversation_state"]["input_understanding"]["field_sources"]["target_supplier_names"] == "conversation_state"
    assert result["conversation_state"]["input_understanding"]["field_sources"]["analysis_dimensions"] == "explicit"


def test_build_execution_context_inherits_dimensions_for_explicit_company_follow_up():
    result = adapter.build_execution_context(
        session_id="session-1",
        user_message="上海汽车制动系统有限公司",
        references=[{"name": "上海汽车制动系统有限公司"}],
        previous_state={
            "current_task": {
                "task_type": "analysis",
                "analysis_dimensions": ["risk", "esg"],
            }
        },
    )

    assert result["current_task"]["target_supplier_names"] == ["上海汽车制动系统有限公司"]
    assert result["current_task"]["analysis_dimensions"] == ["risk", "esg"]


def test_directory_to_plural_risk_to_company_keeps_analysis_task():
    directory_references = [
        {"name": "上海汽车制动系统有限公司", "kind": "supplier", "supplier_id": "sh"},
        {"name": "重庆红旗弹簧有限公司", "kind": "supplier", "supplier_id": "cq"},
    ]
    directory = adapter.build_execution_context(
        session_id="session-1",
        user_message="当前正式供应商有哪些？",
        references=[],
    )
    plural_risk = adapter.build_execution_context(
        session_id="session-1",
        user_message="这些供应商的风险情况有哪些？",
        references=directory_references,
        previous_state=directory["conversation_state"],
    )
    company = adapter.build_execution_context(
        session_id="session-1",
        user_message="上海汽车制动系统有限公司",
        references=directory_references,
        previous_state=plural_risk["conversation_state"],
    )

    assert plural_risk["current_task"]["target_supplier_names"] == [
        "上海汽车制动系统有限公司", "重庆红旗弹簧有限公司",
    ]
    assert plural_risk["current_task"]["analysis_dimensions"] == ["risk"]
    assert company["current_task"]["target_supplier_names"] == ["上海汽车制动系统有限公司"]
    assert company["current_task"]["analysis_dimensions"] == ["risk"]


def test_llm_intent_keeps_risk_filter_as_sourcing_task(monkeypatch):
    payload = {
        "target_supplier_names": [],
        "analysis_dimensions": ["risk"],
        "requested_action": "none",
        "confidence": 0.95,
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "帮我找光电器件领域风险最低的供应商", []
    )

    assert result is not None
    assert result.task_type == "sourcing"
    assert result.analysis_dimensions == ["risk"]


def test_llm_intent_returns_canonical_capability_and_sourcing_slots(monkeypatch):
    payload = {
        "capability": "sourcing",
        "scope": "product_category",
        "sourcing_requirement": {
            "category": "蓄电池",
            "product": "蓄电池",
        },
        "target_supplier_names": [],
        "analysis_dimensions": [],
        "task_type": "none",
        "confidence": 0.97,
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent("帮我找蓄电池供应商", [])

    assert result is not None
    assert result.capability == "sourcing"
    assert result.scope == "product_category"
    assert result.sourcing_requirement is not None
    assert result.sourcing_requirement.category == "蓄电池"


def test_deterministic_intent_marks_sourcing_without_llm():
    assert intent_extractor.infer_task_type("帮我找钢材供应商") == "sourcing"
    assert intent_extractor.infer_task_type("做一下蓄电池的寻源") == "sourcing"
    assert intent_extractor.infer_task_type("看一下蓄电池的供应商") == "sourcing"
    assert intent_extractor.infer_task_type("帮我查一下做蓄电池的供应商") == "sourcing"
    assert intent_extractor.infer_task_type("查询当前正式供应商") == "sourcing"
    assert intent_extractor.infer_task_type("分析甲公司当前风险") == "analysis"
    assert intent_extractor.infer_task_type("复核青岛三祥科技股份有限公司") == "analysis"


@pytest.mark.parametrize(
    "message",
    [
        "把“供应商风险正在上升”翻译成英文",
        "“找供应商”这四个字的英文怎么说？",
        "不需要寻源，只帮我算一下1250除以8",
        "供应商风险分析一般会看哪些指标？",
        "供应商准入流程应该如何制定？",
        "解释一下“风险评分”这个概念，不分析具体供应商",
    ],
)
def test_deterministic_task_inference_ignores_meta_and_general_questions(message):
    assert intent_extractor.infer_task_type(message) == "none"


def test_deterministic_fallback_does_not_turn_quoted_translation_into_supplier_analysis():
    result = intent_extractor._deterministic_extraction_fallback(
        "把“供应商风险正在上升”翻译成英文"
    )

    assert result.task_type == "none"
    assert result.capability == "none"
    assert result.target_supplier_names == []
    assert result.analysis_dimensions == []


def test_recent_financial_risk_keeps_financial_primary_capability():
    assert explicit_primary_capability(
        "分析上海凯众材料科技股份有限公司近三个月的财务风险"
    ) == "financial"
    assert explicit_primary_capability("最近三个月风险怎么变了") == "risk_trend"


def test_llm_general_risk_method_question_does_not_require_supplier_entity(monkeypatch):
    payload = {
        "target_supplier_names": [],
        "analysis_dimensions": ["risk"],
        "capability": "risk",
        "task_type": "analysis",
        "needs_clarification": True,
        "missing_fields": ["target_supplier_names"],
        "interpretation_summary": "用户询问供应商风险分析通常包含哪些指标，属于通用方法咨询。",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "供应商风险分析一般会看哪些指标？", []
    )

    assert result is not None
    assert result.task_type == "none"
    assert result.capability == "none"
    assert result.target_supplier_names == []
    assert result.analysis_dimensions == []
    assert result.needs_clarification is False


def test_llm_declared_no_task_is_not_overridden_by_quoted_domain_words(monkeypatch):
    payload = {
        "target_supplier_names": [],
        "analysis_dimensions": [],
        "capability": "none",
        "task_type": "none",
        "interpretation_summary": "用户请求翻译，与供应商分析无关。",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "把“供应商风险正在上升”翻译成英文", []
    )

    assert result is not None
    assert result.task_type == "none"
    assert result.capability == "none"
    assert result.target_supplier_names == []
    assert result.analysis_dimensions == []


def test_explicit_topic_switch_clears_stale_execution_task_and_pending_clarification():
    context = {
        "session_id": "topic-switch",
        "references": [],
        "conversation_state": {
            "current_task": {
                "task_type": "sourcing",
                "capability": "sourcing",
                "requirement": {"category": "工业相机"},
                "analysis_dimensions": [],
                "subtasks": [{"tool_name": "discover_supplier_candidates"}],
            },
            "pending_clarification": {
                "task_type": "sourcing",
                "missing_fields": ["sourcing_requirement"],
            },
        },
        "current_task": {
            "task_type": "sourcing",
            "capability": "sourcing",
            "requirement": {"category": "工业相机"},
            "analysis_dimensions": [],
            "subtasks": [{"tool_name": "discover_supplier_candidates"}],
            "user_message": "忽略前面的采购分析，告诉我一个冷笑话",
        },
    }
    extraction = intent_extractor.ConversationIntentExtraction(
        capability="none",
        task_type="none",
        requested_action="none",
    )

    result = adapter.apply_extracted_conversation_intent(context, extraction)

    assert result["current_task"]["task_type"] == "none"
    assert result["current_task"].get("subtasks", []) == []
    assert result["current_task"].get("requirement") is None
    assert "pending_clarification" not in result["conversation_state"]


def test_sourcing_preferred_supplier_region_is_separate_from_delivery_region():
    slots = adapter._sourcing_constraint_slots(
        "找5家工业相机供应商，优先华东，预算50万元，交付到苏州"
    )

    assert slots["region"] == "华东"
    assert slots["supply_region"] == "苏州"


def test_sourcing_region_preference_does_not_become_delivery_location():
    context = {
        "current_task": {
            "task_type": "sourcing",
            "llm_sourcing_requirement": {
                "category": "工业相机",
                "product": "工业相机",
                "region": None,
                "supply_region": "华东",
                "budget": "50万元",
                "candidate_count": 5,
            },
        },
        "conversation_state": {},
    }

    result = adapter._apply_harness_sourcing_requirement(
        context,
        "找5家工业相机供应商，优先华东，预算50万元",
    )

    requirement = result["current_task"]["requirement"]
    assert requirement["region"] == "华东"
    assert requirement.get("supply_region") is None


def test_sourcing_region_preference_keeps_prior_delivery_location():
    context = {
        "current_task": {
            "task_type": "sourcing",
            "requirement": {
                "category": "工业相机",
                "specification": "支持GigE接口",
                "supply_region": "苏州",
            },
            "llm_sourcing_requirement": {
                "category": "工业相机",
                "product": "工业相机",
                "specification": "支持GigE接口",
                "region": None,
                "supply_region": "华东",
                "budget": "50万元",
                "candidate_count": 5,
            },
        },
        "conversation_state": {},
    }

    result = adapter._apply_harness_sourcing_requirement(
        context,
        "优先华东，预算50万元",
    )

    requirement = result["current_task"]["requirement"]
    assert requirement["region"] == "华东"
    assert requirement["supply_region"] == "苏州"


def test_deterministic_company_name_resolution_removes_operation_prefixes():
    cases = [
        "帮我分析青岛三祥科技股份有限公司的综合风险",
        "看一下青岛三祥科技股份有限公司的财务数据",
        "核查青岛三祥科技股份有限公司是否有合规风险",
        "查找一下青岛三祥科技股份有限公司的风险",
        "查找纬湃汽车电子 （长春）有限公司的最新舆情和新闻动态",
        "预测青岛三祥科技股份有限公司未来风险趋势",
        "生成青岛三祥科技股份有限公司的风险评估报告",
        "确认青岛三祥科技股份有限公司这个主体",
        "对比青岛三祥科技股份有限公司和上海海拉电子有限公司的风险",
    ]

    for message in cases:
        result = adapter.build_execution_context(
            session_id="prefix-cleanup",
            user_message=message,
        )
        assert result["current_task"]["target_supplier_names"] == (
            ["青岛三祥科技股份有限公司", "上海海拉电子有限公司"]
            if "对比" in message
            else ["纬湃汽车电子（长春）有限公司"]
            if "纬湃" in message
            else ["青岛三祥科技股份有限公司"]
        )


def test_supplier_review_builds_default_real_evidence_task_matrix():
    result = adapter.build_execution_context(
        session_id="review-session",
        user_message="复核青岛三祥科技股份有限公司",
    )

    assert result["current_task"]["target_supplier_names"] == ["青岛三祥科技股份有限公司"]
    assert result["current_task"]["analysis_dimensions"] == [
        "risk", "financial", "business_risk",
    ]
    assert [item["dimension"] for item in result["current_task"]["subtasks"]] == [
        "risk", "financial", "business_risk",
    ]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("查询这家企业的新闻动态", "sentiment"),
        ("有没有负面舆情", "sentiment"),
        ("媒体报道里有什么风险", "sentiment"),
        ("分析上下游关系", "risk_network"),
        ("关联企业是否会产生风险传导", "risk_network"),
        ("查看传染风险", "risk_network"),
        ("查看财务数据", "financial"),
        ("现金流健康吗", "financial"),
        ("净利润下滑了吗", "financial"),
        ("未来 6 个月风险如何", "risk_prediction"),
        ("请做风险预测", "risk_prediction"),
        ("假如营收下降，风险会怎样", "risk_prediction"),
        ("最近三个月风险怎么变了", "risk_trend"),
        ("同比变化如何", "risk_trend"),
        ("和上月相比风险走势如何", "risk_trend"),
        ("核验主体身份", "identity_review"),
        ("用统一社会信用代码查询", "identity_review"),
        ("这些名称是否同一主体", "identity_review"),
        ("查看工商资料", "company_profile"),
        ("给我联系人和官网", "company_profile"),
        ("查询注册资本", "company_profile"),
        ("对比两家供应商", "risk_comparison"),
        ("哪家更适合继续采购", "risk_comparison"),
        ("做一次横向比较", "risk_comparison"),
        ("产品质量稳定吗", "quality"),
        ("不良率高不高", "quality"),
        ("查询质量风险", "quality"),
        ("会不会延期交货", "delivery"),
        ("交期是否稳定", "delivery"),
        ("查看准时交付情况", "delivery"),
        ("寻找供应商", "sourcing"),
        ("找替代厂家", "sourcing"),
        ("推荐供应商", "sourcing"),
        ("生成风险报告", "report"),
        ("导出报告", "report"),
        ("下载风险报告", "report"),
    ],
)
def test_explicit_read_only_expression_normalizes_generic_risk(
    message: str, expected: str
) -> None:
    assert normalize_primary_capability(message, "risk") == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("同时分析财务和舆情", "risk"),
        ("同时分析供应链关系和新闻动态", "risk"),
        ("诉讼和行政处罚有哪些", "risk"),
        # Financial wording is the scenario input; the explicit output asks
        # for a future assessment, so prediction safely owns the lead route.
        ("如果营收下降，未来风险趋势如何", "risk_prediction"),
    ],
)
def test_composite_or_overlapping_expression_keeps_safe_route(
    message: str, expected: str
) -> None:
    assert normalize_primary_capability(message, "risk") == expected


@pytest.mark.parametrize(
    "message",
    [
        "给测试供应商设置每周风险报告",
        "把测试供应商加入监控并生成风险报告",
        "停止刚才正在进行的新闻分析",
        "继续刚才未完成的主体核验",
    ],
)
def test_write_or_durable_control_expression_cannot_be_overridden(message: str) -> None:
    action = intent_extractor.explicit_write_action(message)
    assert normalize_primary_capability(message, "risk", action) == "risk"


@pytest.mark.parametrize(
    ("message", "expected", "task_type", "expected_dimensions"),
    [
        ("分析青岛三祥科技股份有限公司的最新舆情和新闻动态", "sentiment", "analysis", ["sentiment"]),
        ("分析青岛三祥科技股份有限公司的供应链关系和传染风险", "risk_network", "analysis", []),
        ("分析青岛三祥科技股份有限公司的现金流", "financial", "analysis", ["financial"]),
        ("预测青岛三祥科技股份有限公司未来 6 个月的风险", "risk_prediction", "analysis", []),
        ("核验青岛三祥科技股份有限公司的主体身份", "identity_review", "analysis", []),
        ("给我青岛三祥科技股份有限公司的电话和官网", "company_profile", "analysis", []),
        ("比较青岛三祥科技股份有限公司和上海海拉电子有限公司", "risk_comparison", "comparison", []),
        ("分析青岛三祥科技股份有限公司的交付周期", "delivery", "analysis", ["delivery"]),
        ("帮我寻找工业相机的替代供应商", "sourcing", "sourcing", ["risk"]),
        ("生成青岛三祥科技股份有限公司的风险报告", "report", "analysis", []),
    ],
)
def test_llm_generic_risk_is_normalized_for_explicit_read_only_request(
    monkeypatch, message: str, expected: str, task_type: str, expected_dimensions: list[str]
) -> None:
    payload = {
        "capability": "risk",
        "target_supplier_names": ["青岛三祥科技股份有限公司"],
        "analysis_dimensions": ["risk"],
        "task_type": "analysis",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(message, [])

    assert result is not None
    assert result.capability == expected
    assert result.task_type == task_type
    assert result.analysis_dimensions == expected_dimensions


def test_deterministic_fallback_normalizes_explicit_financial_request() -> None:
    result = intent_extractor._deterministic_extraction_fallback(
        "查询“测试-风险供应商”的现金流"
    )

    assert result.capability == "financial"


@pytest.mark.parametrize(
    ("message", "capability", "expected_dimensions"),
    [
        ("分析测试供应商的新闻动态", "sentiment", ["sentiment"]),
        ("分析测试供应商的供应链关系和传染风险", "risk_network", []),
        ("查看测试供应商的现金流", "financial", ["financial"]),
        ("分析测试供应商的交付周期", "delivery", ["delivery"]),
    ],
)
def test_explicit_primary_capability_removes_redundant_risk_dimension(
    message: str, capability: str, expected_dimensions: list[str]
) -> None:
    assert normalize_analysis_dimensions(
        message, ["risk"], capability, capability
    ) == expected_dimensions


def test_explicit_overall_risk_keeps_requested_generic_dimension() -> None:
    assert normalize_analysis_dimensions(
        "分析测试供应商的综合风险和现金流",
        ["risk", "financial"],
        "financial",
        "financial",
    ) == ["risk", "financial"]


def test_explicit_targets_merge_known_reference_hits_and_other_full_names(monkeypatch):
    payload = {
        "target_supplier_names": ["上海凯众材料科技股份有限公司", "青岛三祥科技股份有限公司"],
        "analysis_dimensions": ["financial"],
        "capability": "financial",
        "task_type": "analysis",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)
    result = intent_extractor.extract_conversation_intent(
        "比较上海凯众材料科技股份有限公司和青岛三祥科技股份有限公司的财务表现",
        [{"name": "上海凯众材料科技股份有限公司"}],
    )

    assert result is not None
    assert result.target_supplier_names == ["上海凯众材料科技股份有限公司", "青岛三祥科技股份有限公司"]
    assert result.capability == "risk_comparison"
    assert result.task_type == "comparison"


def test_explicit_compound_capabilities_are_added_to_executable_intent(monkeypatch):
    payload = {
        "target_supplier_names": ["上海凯众材料科技股份有限公司"],
        "analysis_dimensions": ["financial", "risk"],
        "provider_capabilities": [],
        "capability": "financial",
        "task_type": "analysis",
        "interpretation_summary": "同时分析财务和司法风险。",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)
    result = intent_extractor.extract_conversation_intent(
        "分析上海凯众材料科技股份有限公司的财务和司法风险，财务重点看现金流，司法重点看当前未结案件",
        [],
    )

    assert result is not None
    assert result.analysis_dimensions == ["financial"]
    assert "legal_risk" in result.provider_capabilities
    assert result.capability == "financial"


def test_explicit_compound_request_overrides_wrong_llm_primary_capability(monkeypatch):
    payload = {
        "target_supplier_names": ["上海凯众材料科技股份有限公司"],
        "analysis_dimensions": ["financial", "risk"],
        "provider_capabilities": ["legal_risk"],
        "capability": "legal_risk",
        "task_type": "analysis",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)
    result = intent_extractor.extract_conversation_intent(
        "分析上海凯众材料科技股份有限公司的财务和司法风险，财务重点看现金流，司法重点看当前未结案件",
        [],
    )

    assert result is not None
    assert result.capability == "financial"
    assert result.analysis_dimensions == ["financial"]
    assert "legal_risk" in result.provider_capabilities


def test_llm_misplaced_provider_capability_in_dimensions_is_repaired_before_validation():
    normalized = intent_extractor._normalize_llm_payload({
        "analysis_dimensions": ["financial", "legal_risk"],
        "provider_capabilities": [],
    })

    assert normalized["analysis_dimensions"] == ["financial"]
    assert normalized["provider_capabilities"] == ["legal_risk"]


def test_negated_capability_does_not_trigger_semantic_retry(monkeypatch):
    calls = []
    payload = {
        "target_supplier_names": ["甲电机有限公司"],
        "analysis_dimensions": [],
        "provider_capabilities": ["legal_risk"],
        "capability": "legal_risk",
        "task_type": "analysis",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            calls.append(_kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)
    result = intent_extractor.extract_conversation_intent(
        "那先不看舆情了，改查甲电机有限公司的司法风险",
        [],
    )

    assert result is not None
    assert result.capability == "legal_risk"
    assert len(calls) == 1


def test_pending_clarification_slots_survive_company_only_follow_up(monkeypatch):
    payload = {
        "target_supplier_names": ["上海凯众材料科技股份有限公司"],
        "analysis_dimensions": [],
        "provider_capabilities": [],
        "capability": "none",
        "task_type": "none",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)
    result = intent_extractor.extract_conversation_intent(
        "上海凯众材料科技股份有限公司",
        [],
        conversation_context={
            "conversation_state": {
                "pending_clarification": {
                    "task_type": "analysis",
                    "capability": "financial",
                    "target_supplier_names": [],
                    "analysis_dimensions": ["financial"],
                    "provider_capabilities": ["legal_risk"],
                    "missing_fields": ["target_supplier_names"],
                },
            },
        },
    )

    assert result is not None
    assert result.target_supplier_names == ["上海凯众材料科技股份有限公司"]
    assert result.analysis_dimensions == ["financial"]
    assert result.provider_capabilities == ["legal_risk"]


def test_vague_recent_status_request_keeps_a_default_review_task(monkeypatch):
    payload = {
        "target_supplier_names": [],
        "analysis_dimensions": [],
        "capability": "none",
        "task_type": "none",
        "needs_clarification": True,
        "missing_fields": ["target_supplier_names"],
        "clarification_question": "请提供供应商名称。",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)
    result = intent_extractor.extract_conversation_intent("帮我看一下这家公司最近怎么样", [])

    assert result is not None
    assert result.capability == "risk"
    assert result.task_type == "analysis"
    assert result.analysis_dimensions == ["risk", "financial", "business_risk"]
    assert result.needs_clarification is True
    assert result.missing_fields == ["target_supplier_names"]


def test_llm_candidates_do_not_override_ambiguous_singular_context(monkeypatch):
    payload = {
        "target_supplier_names": ["甲电机有限公司", "乙电机有限公司"],
        "analysis_dimensions": ["sentiment"],
        "capability": "sentiment",
        "scope": "supplier_group",
        "task_type": "analysis",
        "requested_action": "none",
        "confidence": 0.95,
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    result = intent_extractor.extract_conversation_intent(
        "它的舆情怎么样？",
        [{"name": "甲电机有限公司"}, {"name": "乙电机有限公司"}],
        conversation_context={
            "session_id": "ambiguous-singular",
            "history": [],
            "conversation_state": {
                "selected_supplier_names": ["甲电机有限公司", "乙电机有限公司"],
            },
        },
    )

    assert result is not None
    assert result.target_supplier_names == []
    assert result.needs_clarification is True
    assert result.missing_fields == ["target_supplier_names"]
    assert result.clarification_question


def test_llm_candidates_do_not_resolve_a_duplicate_supplier_alias(monkeypatch):
    payload = {
        "target_supplier_names": ["上海海拉电子有限公司", "海拉汽车科技有限公司"],
        "analysis_dimensions": ["risk"],
        "capability": "risk",
        "scope": "supplier_group",
        "task_type": "analysis",
        "requested_action": "none",
    }

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)
    result = intent_extractor.extract_conversation_intent(
        "看看海拉的风险。",
        [
            {"name": "上海海拉电子有限公司", "aliases": ["海拉"]},
            {"name": "海拉汽车科技有限公司", "aliases": ["海拉"]},
        ],
        conversation_context={"session_id": "duplicate-alias", "conversation_state": {}},
    )

    assert result is not None
    assert result.target_supplier_names == []
    assert result.needs_clarification is True
    assert result.missing_fields == ["target_supplier_names"]


def test_sourcing_policy_requests_spec_but_accepts_known_history_scope(monkeypatch):
    class FakeCompletions:
        def __init__(self, payload):
            self.payload = payload

        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.payload)))]
            )

    class FakeOpenAI:
        payload = {}

        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions(self.payload))

    monkeypatch.setattr(intent_extractor.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(intent_extractor, "OpenAI", FakeOpenAI)

    FakeOpenAI.payload = {
        "capability": "sourcing", "task_type": "sourcing", "scope": "product_category",
        "sourcing_requirement": {"category": "蓄电池", "specification": "蓄电池"},
        "requested_action": "none",
    }
    underdefined = intent_extractor.extract_conversation_intent(
        "找蓄电池供应商，优先考虑价格。", []
    )
    assert underdefined is not None
    assert underdefined.needs_clarification is True
    assert underdefined.missing_fields == ["sourcing_requirement"]

    FakeOpenAI.payload = {
        "capability": "sourcing", "task_type": "sourcing", "scope": "supplier_directory",
        "sourcing_requirement": {"category": "制动系统", "specification": "制动系统"},
        "requested_action": "none", "needs_clarification": True,
        "ambiguities": ["历史合作范围不明确"],
        "clarification_question": "请说明历史范围。",
    }
    historical = intent_extractor.extract_conversation_intent(
        "帮我找能供应制动系统的历史合作供应商。", []
    )
    assert historical is not None
    assert historical.needs_clarification is False
    assert historical.scope == "product_category"


def test_watchlist_action_overrides_analysis_dimensions_in_execution_task():
    extraction = intent_extractor.ConversationIntentExtraction(
        target_supplier_names=["甲电机有限公司"],
        analysis_dimensions=["risk"],
        capability="watchlist_scope",
        scope="single_supplier",
        task_type="analysis",
        requested_action="add_watchlist",
    )
    context = {
        "session_id": "write-with-risk-dimension",
        "references": [],
        "conversation_state": {},
        "current_task": {
            "task_type": "analysis",
            "target_supplier_names": ["甲电机有限公司"],
            "analysis_dimensions": ["risk"],
            "subtasks": [{"tool_name": "assess_risk"}],
        },
    }

    result = adapter.apply_extracted_conversation_intent(context, extraction)

    assert result["current_task"]["task_type"] == "action_draft"
    assert result["current_task"]["analysis_dimensions"] == ["risk"]
    assert len(result["current_task"]["subtasks"]) == 1


def test_small_talk_does_not_default_to_sourcing_task():
    context = adapter.build_execution_context(
        session_id="small-talk",
        user_message="你好，今天可以帮我做什么？",
        history=[],
        references=[],
        previous_state={},
    )

    assert context["current_task"]["task_type"] == "none"
    assert context["current_task"].get("target_supplier_names", []) == []
    assert context["current_task"].get("analysis_dimensions", []) == []
