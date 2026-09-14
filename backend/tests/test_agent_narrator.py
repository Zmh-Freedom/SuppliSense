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


def test_narrator_falls_back_when_llm_is_not_configured(monkeypatch) -> None:
    answer = _answer()
    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "")

    assert narrate_answer(answer, "复核青岛三祥科技股份有限公司") is answer
