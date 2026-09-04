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


def test_deterministic_intent_marks_sourcing_without_llm():
    assert intent_extractor.infer_task_type("帮我找钢材供应商") == "sourcing"
    assert intent_extractor.infer_task_type("查询当前正式供应商") == "sourcing"
    assert intent_extractor.infer_task_type("分析甲公司当前风险") == "analysis"
