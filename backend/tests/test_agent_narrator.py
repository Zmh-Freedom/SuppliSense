from types import SimpleNamespace

from app.graphs.agent_core.answer_contract import AgentAnswer
from app.graphs.agent_core.evidence_ledger import ValidatedClaim
from app.graphs.agent_core.narrator import (
    NarrativeDraft,
    _validate_draft,
    answer_general_supplier_knowledge,
    narrate_answer,
    translate_user_text,
)


def _answer() -> AgentAnswer:
    return AgentAnswer(
        status="completed",
        summary="规则摘要",
        claims=[ValidatedClaim(
            claim_id="risk-score",
            entity_id="supplier-1",
            dimension="risk",
            statement="青岛三祥科技股份有限公司 综合风险评分：7/100",
            value=7,
            fact_path="risk_score",
            evidence_refs=["risk-evidence"],
            confidence=0.9,
            validation_status="supported",
        )],
    )


def test_narrative_validation_rejects_unverified_number() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown="青岛三祥科技股份有限公司综合风险评分为 92/100。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrative_validation_accepts_formatting_only_number_changes() -> None:
    answer = AgentAnswer(
        status="completed",
        summary="财务摘要",
        claims=[ValidatedClaim(
            claim_id="debt-ratio",
            entity_id="supplier-1",
            dimension="financial",
            statement="青岛三祥科技股份有限公司 资产负债率：43.0%",
            value=0.43,
            fact_path="debt_ratio",
            evidence_refs=["financial-evidence"],
            confidence=0.9,
            validation_status="supported",
        )],
    )
    draft = NarrativeDraft(
        headline="财务分析",
        body_markdown="青岛三祥科技股份有限公司资产负债率为 43%，需要结合现金流继续关注。",
        claim_refs=["debt-ratio"],
    )

    assert _validate_draft(draft, answer, "查看青岛三祥科技股份有限公司的财务数据") is True


def test_narrative_validation_rejects_answer_that_drops_requested_topic() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="供应商复核结果",
        body_markdown="青岛三祥科技股份有限公司当前综合风险评分为 7/100。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer, "分析青岛三祥科技股份有限公司的舆情") is False


def test_narrative_validation_rejects_generic_review_for_comprehensive_risk_question() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown="青岛三祥科技股份有限公司当前综合安全评分为 7/100。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer, "查看青岛三祥科技股份有限公司的风险情况") is False


def test_narrative_validation_requires_sourcing_constraints_from_deterministic_summary() -> None:
    answer = AgentAnswer(
        status="completed",
        summary=(
            "已找到 2 家寻源候选。检索条件：品类：工业相机；目标候选数：5家；"
            "优先地区：华东；预算：50万元；规格：200万像素GigE接口；资质：IATF 16949。"
        ),
        claims=[ValidatedClaim(
            claim_id="sourcing-candidate-count",
            entity_id="sourcing",
            dimension="sourcing",
            statement="已找到2家寻源候选。",
            value=2,
            fact_path="candidate_count",
            evidence_refs=["sourcing-evidence"],
            confidence=0.9,
            validation_status="supported",
        )],
    )
    draft = NarrativeDraft(
        headline="寻源建议",
        body_markdown="已找到2家寻源候选，可查看下方列表。",
        claim_refs=["sourcing-candidate-count"],
    )

    assert _validate_draft(draft, answer, "找5家工业相机供应商，优先华东") is False


def test_narrator_keeps_deterministic_sourcing_summary_and_constraints(monkeypatch) -> None:
    answer = AgentAnswer(
        status="completed",
        summary="已找到2家寻源候选。检索条件：目标候选数：5家；优先地区：华东；预算：50万元。",
        claims=[ValidatedClaim(
            claim_id="sourcing-candidate-count",
            entity_id="sourcing",
            dimension="sourcing",
            statement="已找到2家寻源候选。",
            value=2,
            fact_path="candidate_count",
            evidence_refs=["sourcing-evidence"],
            confidence=0.9,
            validation_status="supported",
        )],
    )

    class UnexpectedNarrator:
        def __init__(self, **_kwargs):
            raise AssertionError("deterministic sourcing answer should not be rewritten")

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", UnexpectedNarrator)

    assert narrate_answer(answer, "找5家华东工业相机供应商，预算50万元") is answer


def test_narrator_uses_only_validated_claims(monkeypatch) -> None:
    answer = _answer()

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content='{"headline":"风险复核结果","body_markdown":"青岛三祥科技股份有限公司当前综合风险评分为7/100。","claim_refs":["risk-score"]}'
                    ))])

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    narrated = narrate_answer(answer, "复核青岛三祥科技股份有限公司")

    assert narrated.summary.startswith("风险复核结果")
    assert "7/100" in narrated.summary


def test_narrator_accepts_provider_json_object_wrapper(monkeypatch) -> None:
    answer = _answer()

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content='{"type":"json_object","headline":"风险复核结果","body_markdown":"青岛三祥科技股份有限公司当前综合风险评分为7/100。","claim_refs":["risk-score"]}'
                    ))])

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    narrated = narrate_answer(answer, "复核青岛三祥科技股份有限公司")

    assert narrated.summary.startswith("风险复核结果")


def test_narrator_falls_back_when_llm_is_not_configured(monkeypatch) -> None:
    answer = _answer()
    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "")

    assert narrate_answer(answer, "复核青岛三祥科技股份有限公司") is answer


def test_narrator_falls_back_when_provider_returns_empty_content(monkeypatch) -> None:
    answer = _answer()

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=""),
                        finish_reason="stop",
                    )], usage=SimpleNamespace(completion_tokens=0))

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    result = narrate_answer(answer, "复核青岛三祥科技股份有限公司")
    assert result.summary == answer.summary
    assert any("润色" in item for item in result.limitations)


def test_narrator_disables_sdk_retries_and_marks_safe_fallback(monkeypatch):
    client_options = []
    calls = []

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    calls.append(_kwargs)
                    raise TimeoutError("provider timeout")

    def create_client(**kwargs):
        client_options.append(kwargs)
        return FakeClient()

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", create_client)

    result = narrate_answer(_answer(), "复核青岛三祥科技股份有限公司")

    assert len(calls) == 1
    assert client_options[0]["max_retries"] == 0
    assert any("润色" in item for item in result.limitations)


def test_general_knowledge_answer_returns_provider_guidance_without_entity_facts(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content='{"answer_markdown":"通常从财务、交付、质量、合规和供应链依赖等维度评估，并结合趋势与整改情况判断。"}'
                    ))])

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    answer = answer_general_supplier_knowledge("供应商风险分析一般看哪些指标？")

    assert "财务" in answer
    assert "供应链依赖" in answer


def test_general_knowledge_answer_rejects_numeric_thresholds(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content='{"answer_markdown":"逾期超过30天就应列为高风险。"}'
                    ))])

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    assert answer_general_supplier_knowledge("供应商风险一般如何评估？") == ""


def test_general_knowledge_answer_allows_standard_identifiers(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content='{"answer_markdown":"质量管理体系可参考 ISO 9001，具体还要结合行业要求评估。"}'
                    ))])

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    assert "ISO 9001" in answer_general_supplier_knowledge("供应商风险评估一般看什么？")


def test_translate_user_text_returns_model_translation_without_rewriting_content(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content='{"answer_markdown":"Supplier risk is increasing."}'
                    ))])

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    assert translate_user_text("把‘供应商风险正在上升’翻译成英文") == "Supplier risk is increasing."
