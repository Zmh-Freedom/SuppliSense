"""Regression tests for LLM-first conversation intent extraction."""

import json
from types import SimpleNamespace

from app.graphs.agent_core import adapter
from app.graphs.agent_core import intent_extractor


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


def test_llm_extractor_normalizes_spoken_filler_in_explicit_company_name(monkeypatch):
    """Model wording must not turn ``一下`` into part of the legal name."""
    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                    "target_supplier_names": ["一下青岛三祥科技股份有限公司"],
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
        "查看一下青岛三祥科技股份有限公司的风险情况",
        [],
    )

    assert result is not None
    assert result.target_supplier_names == ["青岛三祥科技股份有限公司"]


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
