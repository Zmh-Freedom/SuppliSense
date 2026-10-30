import json
from types import SimpleNamespace

import pytest

from app.graphs.agent_core.answer_contract import AgentAnswer
from app.graphs.agent_core.evidence_ledger import ValidatedClaim
from app.graphs.agent_core.narrator import (
    NarrativeDraft,
    _finding_polarities,
    _is_company_profile_answer,
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


def _profile_answer() -> AgentAnswer:
    company = "青岛三祥科技股份有限公司"
    return AgentAnswer(
        status="partial",
        summary=(
            f"工商资料：{company}法定代表人：张敏；注册资本：5000万元；"
            "股东：甲股东、乙股东；对外投资：未查询；历史变更：查询失败；"
            "分支机构：明确无记录；重大诉讼情况无法确认。"
        ),
        claims=[
            ValidatedClaim(
                claim_id="profile-legal-person",
                entity_id="supplier-profile-1",
                dimension="company_profile",
                statement=f"{company}法定代表人：张敏",
                value="张敏",
                fact_path="profile.baseinfo.legalPersonName",
                evidence_refs=["profile-evidence"],
                confidence=0.9,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="profile-capital",
                entity_id="supplier-profile-1",
                dimension="company_profile",
                statement=f"{company}注册资本：5000万元",
                value="5000万元",
                fact_path="profile.baseinfo.regCapital",
                evidence_refs=["profile-evidence"],
                confidence=0.9,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="profile-holder-a",
                entity_id="supplier-profile-1",
                dimension="company_profile",
                statement=f"{company}股东：甲股东",
                value="甲股东",
                fact_path="profile.holder_names",
                evidence_refs=["profile-evidence"],
                confidence=0.9,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="profile-holder-b",
                entity_id="supplier-profile-1",
                dimension="company_profile",
                statement=f"{company}股东：乙股东",
                value="乙股东",
                fact_path="profile.holder_names",
                evidence_refs=["profile-evidence"],
                confidence=0.9,
                validation_status="supported",
            ),
        ],
        limitations=["对外投资未查询，当前无法确认其情况"],
        evidence_refs=["profile-evidence"],
    )


def test_profile_layout_accepts_count_only_profile_domains() -> None:
    answer = _profile_answer()
    answer.claims.append(ValidatedClaim(
        claim_id="profile-branch-count",
        entity_id="supplier-profile-1",
        dimension="company_profile",
        statement="青岛三祥科技股份有限公司工商资料，分支机构：明确无记录",
        value=0,
        fact_path="counts.branch",
        evidence_refs=["profile-evidence"],
        confidence=0.9,
        validation_status="supported",
    ))

    assert _is_company_profile_answer(answer) is True


def _fake_narrator_client(body_markdown: str, prompt_capture: list[dict] | None = None):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    if prompt_capture is not None:
                        prompt_capture.append(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content=json.dumps({
                            "headline": "工商资料",
                            "body_markdown": body_markdown,
                            "claim_refs": [
                                "profile-legal-person",
                                "profile-capital",
                                "profile-holder-a",
                                "profile-holder-b",
                            ],
                        }, ensure_ascii=False)
                    ))])

    return FakeClient


def _capture_narration_events(monkeypatch) -> list[tuple[str, str, dict]]:
    events: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "app.graphs.agent_core.narrator.logger",
        SimpleNamespace(
            info=lambda event, **fields: events.append(("info", event, fields)),
            warning=lambda event, **fields: events.append(("warning", event, fields)),
        ),
    )
    return events


def test_narrative_validation_rejects_unverified_number() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown="青岛三祥科技股份有限公司综合风险评分为 92/100。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrative_validation_rejects_reordered_supported_numbers() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown="青岛三祥科技股份有限公司综合风险评分为 100/7。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrative_validation_rejects_omitted_structured_limitation() -> None:
    answer = _answer().model_copy(update={
        "status": "needs_review",
        "limitations": ["财务数据未覆盖，无法判断现金流风险"],
    })
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown="青岛三祥科技股份有限公司综合风险评分为 7/100。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrative_validation_rejects_reversed_verified_conclusion() -> None:
    claim = ValidatedClaim(
        claim_id="major-lawsuit",
        entity_id="supplier-1",
        dimension="legal_risk",
        statement="青岛三祥科技股份有限公司存在重大诉讼标记。",
        value=True,
        fact_path="major_lawsuit",
        evidence_refs=["legal-evidence"],
        confidence=0.9,
        validation_status="supported",
    )
    answer = AgentAnswer(
        status="completed",
        summary="青岛三祥科技股份有限公司存在重大诉讼标记。",
        claims=[claim],
    )
    draft = NarrativeDraft(
        headline="司法风险核验",
        body_markdown="青岛三祥科技股份有限公司未发现重大诉讼。",
        claim_refs=["major-lawsuit"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrative_validation_rejects_negative_conclusion_from_uncertain_source() -> None:
    answer = _answer().model_copy(update={
        "summary": "青岛三祥科技股份有限公司的司法信息无法确认是否存在重大诉讼。",
    })
    draft = NarrativeDraft(
        headline="司法风险核验",
        body_markdown="青岛三祥科技股份有限公司未发现重大诉讼。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrative_validation_rejects_risk_level_from_uncertain_source() -> None:
    answer = _answer().model_copy(update={
        "summary": "青岛三祥科技股份有限公司无法确认是否存在重大诉讼。",
    })
    draft = NarrativeDraft(
        headline="司法风险核验",
        body_markdown="重大诉讼风险较高。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrator_keeps_an_uncertain_legal_conclusion_deterministic(monkeypatch) -> None:
    answer = _answer().model_copy(update={
        "summary": "青岛三祥科技股份有限公司无法确认是否存在重大诉讼。",
    })

    class UnexpectedNarrator:
        def __init__(self, **_kwargs):
            raise AssertionError("uncertain legal conclusions must not be rewritten by the model")

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", UnexpectedNarrator)

    assert narrate_answer(answer, "查询重大诉讼") is answer


def test_narrator_uses_model_markdown_layout_for_partial_company_profile(monkeypatch) -> None:
    answer = _profile_answer()
    prompt_capture: list[dict] = []
    body = (
        "### 企业基本信息\n\n"
        "- 青岛三祥科技股份有限公司法定代表人：张敏\n"
        "- 青岛三祥科技股份有限公司注册资本：5000万元\n\n"
        "### 股东信息\n\n"
        "- 青岛三祥科技股份有限公司股东：甲股东\n"
        "- 青岛三祥科技股份有限公司股东：乙股东\n\n"
        "### 查询边界\n\n"
        "- 对外投资：未查询\n"
        "- 历史变更：查询失败\n"
        "- 分支机构：明确无记录\n"
        "- 对外投资未查询，当前无法确认其情况\n"
        "- 重大诉讼情况无法确认"
    )

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.graphs.agent_core.narrator.OpenAI",
        lambda **_kwargs: _fake_narrator_client(body, prompt_capture),
    )

    narrated = narrate_answer(answer, "查询青岛三祥科技股份有限公司工商资料")

    assert len(prompt_capture) == 1
    assert narrated.summary.startswith("工商资料\n\n")
    assert "### 企业基本信息" in narrated.summary
    assert "### 股东信息" in narrated.summary
    assert "对外投资：未查询" in narrated.summary
    assert "历史变更：查询失败" in narrated.summary
    assert "分支机构：明确无记录" in narrated.summary
    assert "重大诉讼情况无法确认" in narrated.summary
    prompt = json.loads(prompt_capture[0]["messages"][1]["content"])
    assert prompt["presentation_mode"] == "company_profile_markdown_layout"
    assert any("不得新增来源、URL 或 Markdown 链接" in rule for rule in prompt["rules"])


def test_narrator_keeps_profile_layout_after_subject_confirmation_reply(monkeypatch) -> None:
    answer = _profile_answer()
    prompt_capture: list[dict] = []
    body = (
        "### 企业基本信息\n\n"
        "- 青岛三祥科技股份有限公司法定代表人：张敏\n"
        "- 青岛三祥科技股份有限公司注册资本：5000万元\n\n"
        "### 股东信息\n\n"
        "- 青岛三祥科技股份有限公司股东：甲股东\n"
        "- 青岛三祥科技股份有限公司股东：乙股东\n\n"
        "### 查询边界\n\n"
        "- 对外投资：未查询\n"
        "- 历史变更：查询失败\n"
        "- 分支机构：明确无记录\n"
        "- 对外投资未查询，当前无法确认其情况\n"
        "- 重大诉讼情况无法确认"
    )

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.graphs.agent_core.narrator.OpenAI",
        lambda **_kwargs: _fake_narrator_client(body, prompt_capture),
    )

    narrated = narrate_answer(answer, "青岛三祥科技股份有限公司")

    assert len(prompt_capture) == 1
    assert "### 股东信息" in narrated.summary
    assert "重大诉讼情况无法确认" in narrated.summary


def test_narrator_rejects_profile_layout_that_changes_an_uncertain_conclusion(monkeypatch) -> None:
    answer = _profile_answer()
    prompt_capture: list[dict] = []
    log_events = _capture_narration_events(monkeypatch)
    body = (
        "### 企业基本信息\n\n"
        "- 青岛三祥科技股份有限公司法定代表人：张敏\n"
        "- 青岛三祥科技股份有限公司注册资本：5000万元\n\n"
        "### 股东信息\n\n"
        "- 青岛三祥科技股份有限公司股东：甲股东\n"
        "- 青岛三祥科技股份有限公司股东：乙股东\n\n"
        "### 查询边界\n\n"
        "- 对外投资：未查询\n"
        "- 历史变更：查询失败\n"
        "- 分支机构：明确无记录\n"
        "- 对外投资未查询，当前无法确认其情况\n"
        "- 重大诉讼未发现"
    )

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.graphs.agent_core.narrator.OpenAI",
        lambda **_kwargs: _fake_narrator_client(body, prompt_capture),
    )

    narrated = narrate_answer(answer, "查询青岛三祥科技股份有限公司工商资料")

    assert len(prompt_capture) == 1
    assert "重大诉讼未发现" not in narrated.summary
    assert "重大诉讼情况无法确认" in narrated.summary
    assert "工商资料" in narrated.summary
    assert "模型排版" not in narrated.summary
    assert any(
        level == "warning" and event == "answer_narration_degraded"
        and fields["fallback_mode"] == "validated_company_profile_markdown"
        for level, event, fields in log_events
    )


def test_narrator_rejects_profile_layout_that_injects_a_link(monkeypatch) -> None:
    answer = _profile_answer()
    prompt_capture: list[dict] = []
    log_events = _capture_narration_events(monkeypatch)
    body = (
        "### 企业基本信息\n\n"
        "- 青岛三祥科技股份有限公司法定代表人：张敏\n"
        "- 青岛三祥科技股份有限公司注册资本：5000万元\n\n"
        "### 股东信息\n\n"
        "- 青岛三祥科技股份有限公司股东：甲股东\n"
        "- 青岛三祥科技股份有限公司股东：乙股东\n\n"
        "### 查询边界\n\n"
        "- 对外投资：未查询\n"
        "- 历史变更：查询失败\n"
        "- 分支机构：明确无记录\n"
        "- 对外投资未查询，当前无法确认其情况\n"
        "- 重大诉讼情况无法确认\n"
        "[外部来源](https://example.invalid/profile)"
    )

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.graphs.agent_core.narrator.OpenAI",
        lambda **_kwargs: _fake_narrator_client(body, prompt_capture),
    )

    narrated = narrate_answer(answer, "查询青岛三祥科技股份有限公司工商资料")

    assert len(prompt_capture) == 1
    assert narrated.summary != answer.summary
    assert "### 股东信息" in narrated.summary
    assert "https://" not in narrated.summary
    assert "模型排版" not in narrated.summary
    assert any(
        level == "warning" and event == "answer_narration_degraded"
        and fields["reason"] == "narrative_link_not_allowed"
        for level, event, fields in log_events
    )


def test_narrator_uses_claim_based_markdown_when_model_is_not_configured(monkeypatch) -> None:
    answer = _profile_answer()
    log_events = _capture_narration_events(monkeypatch)

    class UnexpectedNarrator:
        def __init__(self, **_kwargs):
            raise AssertionError("no configured model should be called")

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", UnexpectedNarrator)

    narrated = narrate_answer(answer, "查询青岛三祥科技股份有限公司工商资料")

    assert narrated.summary.startswith("工商资料\n\n")
    assert "- 青岛三祥科技股份有限公司法定代表人：张敏" in narrated.summary
    assert "- 青岛三祥科技股份有限公司股东：甲股东" in narrated.summary
    assert "对外投资：未查询" in narrated.summary
    assert "历史变更：查询失败" in narrated.summary
    assert "分支机构：明确无记录" in narrated.summary
    assert "重大诉讼情况无法确认" in narrated.summary
    assert narrated.limitations == answer.limitations
    assert "模型" not in narrated.summary
    assert any(
        level == "info" and event == "answer_narration_skipped"
        and fields["reason"] == "missing_api_key"
        and fields["fallback_mode"] == "validated_company_profile_markdown"
        for level, event, fields in log_events
    )


def test_profile_fallback_focuses_follow_up_on_requested_domain(monkeypatch) -> None:
    answer = _profile_answer()

    class UnexpectedNarrator:
        def __init__(self, **_kwargs):
            raise AssertionError("no configured model should be called")

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", UnexpectedNarrator)

    narrated = narrate_answer(answer, "它的股东有哪些")

    assert "### 股东信息" in narrated.summary
    assert "甲股东" in narrated.summary
    assert "乙股东" in narrated.summary
    assert "法定代表人" not in narrated.summary
    assert "注册资本" not in narrated.summary
    assert "对外投资" not in narrated.summary


def test_finding_polarity_uses_the_clause_nearest_to_the_topic() -> None:
    text = "财务无法确认，但未发现重大诉讼。"

    assert _finding_polarities(text, "重大诉讼") == {"negative"}


def test_narrative_validation_rejects_unverified_qualitative_conclusion() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="司法风险核验",
        body_markdown="青岛三祥科技股份有限公司存在重大诉讼。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrative_validation_rejects_unverified_supplier_health_assertions() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown=(
            "青岛三祥科技股份有限公司综合风险评分为 7/100。"
            "现金流充裕，交付稳定，质量控制良好。"
        ),
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrative_validation_does_not_use_hidden_headline_for_requested_topic() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="财务数据核验",
        body_markdown="青岛三祥科技股份有限公司 综合风险评分：7/100",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer, "查看青岛三祥科技股份有限公司的财务数据") is False


def test_narrator_falls_back_when_model_adds_unverified_supplier_health(monkeypatch) -> None:
    answer = _answer()

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content=(
                            '{"headline":"风险复核结果",'
                            '"body_markdown":"青岛三祥科技股份有限公司综合风险评分为 7/100。'
                            '现金流充裕，交付稳定，质量控制良好。",'
                            '"claim_refs":["risk-score"]}'
                        )
                    ))])

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    narrated = narrate_answer(answer, "复核青岛三祥科技股份有限公司")

    assert narrated.summary == answer.summary


@pytest.mark.parametrize("unsupported_clause", ["现金流风险很高", "暂停采购"])
def test_narrative_validation_does_not_let_limitation_cover_neighbor_clause(
    unsupported_clause: str,
) -> None:
    answer = _answer().model_copy(update={
        "status": "needs_review",
        "limitations": ["缺少 financial 维度的正式证据"],
    })
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown=(
            "青岛三祥科技股份有限公司综合风险评分为 7/100。"
            f"缺少 financial 维度的正式证据，{unsupported_clause}。"
        ),
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrative_validation_rejects_extra_assertion_inside_limitation_clause() -> None:
    answer = _answer().model_copy(update={
        "status": "needs_review",
        "limitations": ["缺少 financial 维度的正式证据"],
    })
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown=(
            "青岛三祥科技股份有限公司综合风险评分为 7/100。"
            "缺少 financial 维度的正式证据并且现金流风险很高。"
        ),
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrator_falls_back_when_model_hides_claim_behind_limitation(monkeypatch) -> None:
    answer = _answer().model_copy(update={
        "status": "needs_review",
        "limitations": ["缺少 financial 维度的正式证据"],
    })

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content=(
                            '{"headline":"风险复核结果",'
                            '"body_markdown":"青岛三祥科技股份有限公司综合风险评分为 7/100。'
                            '缺少 financial 维度的正式证据，现金流风险很高。",'
                            '"claim_refs":["risk-score"]}'
                        )
                    ))])

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    narrated = narrate_answer(answer, "复核青岛三祥科技股份有限公司")

    assert narrated.summary == answer.summary


def test_narrative_validation_rejects_unsupported_procurement_recommendation() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="风险分析结果",
        body_markdown="青岛三祥科技股份有限公司综合风险评分为 7/100，建议立即扩大采购份额。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer, "分析青岛三祥科技股份有限公司的风险") is False


def test_narrative_validation_rejects_unsupported_imperative_procurement_action() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown="青岛三祥科技股份有限公司综合风险评分为 7/100。请立即暂停采购。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrator_falls_back_when_model_issues_unapproved_procurement_instruction(monkeypatch) -> None:
    answer = _answer()

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content=(
                            '{"headline":"风险复核结果",'
                            '"body_markdown":"青岛三祥科技股份有限公司综合风险评分为 7/100。'
                            '请立即暂停采购。",'
                            '"claim_refs":["risk-score"]}'
                        )
                    ))])

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    narrated = narrate_answer(answer, "复核青岛三祥科技股份有限公司")

    assert narrated.summary == answer.summary


def test_narrative_validation_allows_evidence_supported_procurement_review() -> None:
    answer = _answer().model_copy(update={
        "action_proposals": [{
            "action_type": "procurement_review",
            "label": "安排采购复核",
            "reason": "已发现需要人工确认的风险信号，先核实证据再决定是否调整采购策略。",
        }],
    })
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown=(
            "青岛三祥科技股份有限公司综合风险评分为 7/100，"
            "建议安排采购复核。"
        ),
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is True


def test_narrative_validation_rejects_unverified_follow_up_guidance() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="风险分析结果",
        body_markdown=(
            "青岛三祥科技股份有限公司综合风险评分为 7/100，"
            "建议继续关注风险变化，暂不据此调整采购策略。"
        ),
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


@pytest.mark.parametrize(
    ("source_summary", "narrative"),
    [
        (
            "青岛三祥科技股份有限公司舆情来源查询失败，无法确认是否存在相关新闻。",
            "青岛三祥科技股份有限公司舆情查询已完成，已确认未发现相关新闻。",
        ),
        (
            "青岛三祥科技股份有限公司舆情检索已完成，已确认未发现相关新闻。",
            "青岛三祥科技股份有限公司舆情来源暂不可用，无法确认相关新闻情况。",
        ),
    ],
)
def test_narrative_validation_preserves_source_failure_and_confirmed_empty_distinction(
    source_summary: str, narrative: str
) -> None:
    answer = _answer().model_copy(update={
        "status": "partial",
        "summary": source_summary,
        "limitations": [],
    })
    draft = NarrativeDraft(
        headline="舆情查询结果",
        body_markdown=narrative,
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer, "查看青岛三祥科技股份有限公司舆情") is False


def test_narrative_validation_accepts_natural_paraphrase_of_limitation() -> None:
    answer = _answer().model_copy(update={
        "status": "needs_review",
        "limitations": ["财务数据未覆盖，无法判断现金流风险"],
    })
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown=(
            "青岛三祥科技股份有限公司综合风险评分为 7/100。"
            "目前财务资料覆盖不足，暂不能判断现金流风险。"
        ),
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is True


def test_narrative_validation_rejects_limitation_that_drops_missing_objects_and_impact() -> None:
    answer = _answer().model_copy(update={
        "status": "needs_review",
        "limitations": ["缺少现金流量表和审计报告，无法判断短期偿债能力"],
    })
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown="青岛三祥科技股份有限公司综合风险评分为 7/100，财务数据覆盖不足。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrative_validation_rejects_limitation_that_drops_not_yet_received_document() -> None:
    answer = _answer().model_copy(update={
        "status": "needs_review",
        "limitations": ["现金流量表尚未收到，无法判断短期偿债能力"],
    })
    draft = NarrativeDraft(
        headline="风险复核结果",
        body_markdown="青岛三祥科技股份有限公司财务数据覆盖不足，无法判断短期偿债能力。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrative_validation_rejects_unverified_number_in_headline() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="风险评分 999 分",
        body_markdown="青岛三祥科技股份有限公司综合风险评分为 7/100。",
        claim_refs=["risk-score"],
    )

    assert _validate_draft(draft, answer) is False


def test_narrative_validation_rejects_unverified_company_in_headline() -> None:
    answer = _answer()
    draft = NarrativeDraft(
        headline="虚构材料有限公司风险复核",
        body_markdown="青岛三祥科技股份有限公司综合风险评分为 7/100。",
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
        body_markdown="青岛三祥科技股份有限公司资产负债率为 43%。",
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
                        content='{"headline":"现金流充裕","body_markdown":"青岛三祥科技股份有限公司 综合风险评分：7/100","claim_refs":["risk-score"]}'
                    ))])

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    narrated = narrate_answer(answer, "复核青岛三祥科技股份有限公司")

    assert narrated.summary.startswith("核验结果")
    assert "现金流充裕" not in narrated.summary
    assert "7/100" in narrated.summary


def test_narrator_falls_back_when_model_omits_structured_limitation(monkeypatch) -> None:
    answer = _answer().model_copy(update={
        "status": "needs_review",
        "summary": "青岛三祥科技股份有限公司综合风险评分为 7/100，财务数据未覆盖。",
        "limitations": ["财务数据未覆盖，无法判断现金流风险"],
    })

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content=(
                            '{"headline":"风险复核结果",'
                            '"body_markdown":"青岛三祥科技股份有限公司当前综合风险评分为7/100。",'
                            '"claim_refs":["risk-score"]}'
                        )
                    ))])

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    narrated = narrate_answer(answer, "复核青岛三祥科技股份有限公司的风险")

    assert narrated.summary == answer.summary
    assert "财务数据未覆盖，无法判断现金流风险" in narrated.limitations


def test_narrator_accepts_provider_json_object_wrapper(monkeypatch) -> None:
    answer = _answer()

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content='{"type":"json_object","headline":"风险复核结果","body_markdown":"青岛三祥科技股份有限公司 综合风险评分：7/100","claim_refs":["risk-score"]}'
                    ))])

    monkeypatch.setattr("app.graphs.agent_core.narrator.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.graphs.agent_core.narrator.OpenAI", lambda **_kwargs: FakeClient())

    narrated = narrate_answer(answer, "复核青岛三祥科技股份有限公司")

    assert narrated.summary.startswith("核验结果")


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
