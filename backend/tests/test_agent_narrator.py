from types import SimpleNamespace

from app.graphs.agent_core.answer_contract import AgentAnswer
from app.graphs.agent_core.evidence_ledger import ValidatedClaim
from app.graphs.agent_core.narrator import NarrativeDraft, _validate_draft, narrate_answer


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
