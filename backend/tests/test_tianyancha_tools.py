from __future__ import annotations

from app.domains.risk import tools_tianyancha
from app.tools import TOOL_REGISTRY


COMPANY = "示例汽车零部件有限公司"


def test_tianyancha_tools_are_registered_as_read_only_evidence_tools() -> None:
    expected = {
        "lookup_company_identity",
        "lookup_legal_risk",
        "lookup_business_risk",
        "lookup_company_news",
        "lookup_company_profile",
    }
    registered = {definition.spec.name for definition in TOOL_REGISTRY.definitions()}
    assert expected <= registered
    for name in expected:
        definition = TOOL_REGISTRY.get(name)
        assert definition is not None
        assert definition.spec.side_effect == "read"
        assert definition.spec.approval_policy == "none"
        assert definition.spec.evidence_required is True


def test_lookup_company_identity_returns_candidate_and_strict_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        tools_tianyancha,
        "_ensure_documents",
        lambda *_args, **_kwargs: (
            {"baseinfo": {"items": {"result": {
                "id": "tyc-1",
                "name": COMPANY,
                "creditCode": "91370000TEST",
                "legalPersonName": "张三",
                "regStatus": "存续",
            }}}},
            "cached",
            "已使用本地天眼查快照",
        ),
    )

    result = tools_tianyancha.lookup_company_identity.invoke({"company_name": COMPANY})
    validated = TOOL_REGISTRY.get("lookup_company_identity").output_model.model_validate(result)

    assert validated.resolution == "exact"
    assert validated.candidate["unified_social_credit_code"] == "91370000TEST"
    assert validated.evidence_records
    assert "evidence" not in result


def test_lookup_legal_risk_returns_counts_and_records(monkeypatch) -> None:
    monkeypatch.setattr(
        tools_tianyancha,
        "_ensure_documents",
        lambda *_args, **_kwargs: (
            {
                "lawSuit": {"items": {"result": {"items": [{"caseNo": "A-1"}]}}},
                "executedPerson": {"items": {"result": {"items": [{"name": COMPANY}]}}},
                "dishonesty": {"items": {"result": {"items": []}}},
            },
            "cached",
            "已使用本地天眼查快照",
        ),
    )

    result = tools_tianyancha.lookup_legal_risk.invoke({"company_name": COMPANY})
    validated = TOOL_REGISTRY.get("lookup_legal_risk").output_model.model_validate(result)

    assert validated.domain == "legal_risk"
    assert validated.counts["lawSuit"] == 1
    assert validated.counts["executedPerson"] == 1
    assert validated.claims
    assert validated.evidence_records


def test_lookup_business_risk_without_provider_data_stays_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        tools_tianyancha,
        "_ensure_documents",
        lambda *_args, **_kwargs: ({}, "unavailable", "未配置天眼查访问凭据"),
    )

    result = tools_tianyancha.lookup_business_risk.invoke({"company_name": COMPANY})
    validated = TOOL_REGISTRY.get("lookup_business_risk").output_model.model_validate(result)

    assert validated.status == "unavailable"
    assert validated.counts == {name: 0 for name in tools_tianyancha._BUSINESS_COLLECTIONS}
    assert validated.claims == []
    assert "不能形成该维度结论" in validated.limitations[0]


def test_lookup_company_news_and_profile_validate(monkeypatch) -> None:
    def fake_ensure(_name, collections, **_kwargs):
        if collections == ("news",):
            return {"news": {"items": {"result": {"items": [{"title": "公告"}]}}}}, "cached", "已使用本地天眼查快照"
        return {
            "baseinfo": {"items": {"result": {"regCapital": "1000万", "regLocation": "上海"}}},
            "holder": {"items": {"result": {"items": [{"name": "股东甲"}]}}},
        }, "cached", "已使用本地天眼查快照"

    monkeypatch.setattr(tools_tianyancha, "_ensure_documents", fake_ensure)
    news = tools_tianyancha.lookup_company_news.invoke({"company_name": COMPANY})
    profile = tools_tianyancha.lookup_company_profile.invoke({"company_name": COMPANY})

    news_output = TOOL_REGISTRY.get("lookup_company_news").output_model.model_validate(news)
    profile_output = TOOL_REGISTRY.get("lookup_company_profile").output_model.model_validate(profile)
    assert news_output.articles_count == 1
    assert profile_output.profile["baseinfo"]["regCapital"] == "1000万"
    assert profile_output.claims
