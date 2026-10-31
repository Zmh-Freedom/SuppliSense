from __future__ import annotations

import asyncio

import pytest

from app.domains.risk import tools_tianyancha
from app.core.config import settings
from app.graphs.harness import ExecutionBudget, run_harness
from app.tools import TOOL_REGISTRY
from app.tools.executor import ToolExecutor


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


def test_lookup_legal_risk_exposes_each_judicial_collection_status(monkeypatch) -> None:
    monkeypatch.setattr(
        tools_tianyancha,
        "_ensure_documents",
        lambda *_args, **_kwargs: (
            {
                "executedPerson": {"items": {"result": {"items": [{"name": COMPANY}]}}},
                "dishonesty": {"items": {"result": {"total": 0, "items": [], "error_code": 300000}}},
            },
            "partial",
            "已完成天眼查缺失维度检索并写入本地快照",
            {"lawSuit": "query_failed", "courtAnnouncement": "not_queried", "consumptionRestriction": "queried"},
        ),
    )

    result = tools_tianyancha.lookup_legal_risk.invoke({"company_name": COMPANY})
    validated = TOOL_REGISTRY.get("lookup_legal_risk").output_model.model_validate(result)

    assert validated.collection_statuses["executedPerson"]["status"] == "has_records"
    assert validated.collection_statuses["dishonesty"]["status"] == "no_records"
    assert validated.collection_statuses["courtAnnouncement"]["status"] == "not_queried"
    assert validated.collection_statuses["consumptionRestriction"]["status"] == "query_failed"
    assert {item["data_type"] for item in validated.records} >= set(tools_tianyancha._LEGAL_COLLECTIONS)
    assert validated.evidence_records[0]["status"] == "available"


def test_missing_cached_legal_collection_is_refreshed_independently(monkeypatch) -> None:
    law_suit = {"items": {"result": {"total": 0, "items": [], "error_code": 300000}}}
    executed = {"items": {"result": {"items": [{"name": COMPANY}]}}}
    cache = {"lawSuit": law_suit}
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(
        tools_tianyancha,
        "_cached_documents",
        lambda _name, collections: {collection: cache[collection] for collection in collections if collection in cache},
    )
    monkeypatch.setattr(settings, "TIANYANCHA_TOKEN", "test-token")

    def fake_fetch(_name: str, collections: list[str] | tuple[str, ...]) -> dict[str, str]:
        calls.append(tuple(collections))
        cache["executedPerson"] = executed
        return {collection: "queried" for collection in collections}

    from app.services import tianyancha_client
    monkeypatch.setattr(tianyancha_client, "fetch_collections", fake_fetch)

    documents, source_mode, _message, statuses = tools_tianyancha._ensure_documents(
        COMPANY, ("lawSuit", "executedPerson")
    )

    assert calls == [("executedPerson",)]
    assert source_mode == "live"
    assert documents["executedPerson"] == executed
    assert statuses == {"executedPerson": "queried"}


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


def test_profile_no_result_provider_envelope_is_not_counted_as_a_record() -> None:
    document = {
        "name": COMPANY,
        "supplier_id": "supplier-1",
        "items": {"error_code": 300000, "reason": "没有查询到结果", "result": {}},
    }

    assert tools_tianyancha._records(document) == []
    assert tools_tianyancha._collection_status(document, "queried") == "no_records"


def _partial_profile_fixture() -> tuple[dict, str, str, dict[str, str]]:
    return (
        {
            "baseinfo": {"name": COMPANY, "items": {"error_code": 0, "reason": "ok", "result": {
                "name": COMPANY,
                "creditCode": "91370200TEST001",
                "legalPersonName": "张三",
                "regStatus": "存续",
                "regCapital": "12000万",
                "regLocation": "山东省青岛市",
                "estiblishTime": 1704067200000,
            }}},
            "holder": {"name": COMPANY, "items": {"error_code": 0, "reason": "ok", "result": {"items": [
                {"name": "甲投资控股有限公司"},
                {"name": "乙产业投资有限公司"},
            ]}}},
            "branch": {"name": COMPANY, "items": {"error_code": 300000, "reason": "没有查询到结果", "result": {}}},
        },
        "partial",
        "已完成天眼查部分维度检索",
        {
            "invest": "query_failed",
            "changeInfo": "not_queried",
            "branch": "queried",
        },
    )


@pytest.mark.agent_e2e
def test_partial_profile_fields_and_domain_states_survive_tool_harness_answer(monkeypatch) -> None:
    monkeypatch.setattr(tools_tianyancha, "_ensure_documents", lambda *_args, **_kwargs: _partial_profile_fixture())
    user_message = f"查询{COMPANY}的工商资料"
    state = {
        "session_id": "profile-session",
        "turn_id": "profile-turn",
        "run_id": "profile-run",
        "user_message": user_message,
        "execution_context": {"references": []},
        "current_task": {
            "task_type": "analysis",
            "capability": "company_profile",
            "scope": "single_supplier",
            "target_supplier_names": [COMPANY],
            "analysis_dimensions": [],
            "provider_capabilities": ["profile"],
            "user_message": user_message,
        },
        "task_specs": [{
            "task_id": "profile",
            "tool_name": "lookup_company_profile",
            "arguments": {"company_name": COMPANY},
            "entity_id": f"entity:{COMPANY}",
            "dimension": "company_profile",
            "required": True,
            "evidence_requirements": ["company_profile"],
        }],
        "budget": ExecutionBudget().model_dump(mode="json"),
    }

    result = asyncio.run(run_harness(state, executor=ToolExecutor(TOOL_REGISTRY)))

    outcome = next(item for item in result["tool_outcomes"] if item["tool_name"] == "lookup_company_profile")
    data = outcome["data"]
    statuses = data["collection_statuses"]
    assert data["source_mode"] == "partial"
    assert statuses["baseinfo"]["status"] == "has_records"
    assert statuses["holder"]["status"] == "has_records"
    assert statuses["baseinfo"]["source_mode"] == "cached"
    assert statuses["holder"]["source_mode"] == "cached"
    assert statuses["invest"]["status"] == "query_failed"
    assert statuses["changeInfo"]["status"] == "not_queried"
    assert statuses["branch"]["status"] == "no_records"
    assert data["counts"]["branch"] == 0
    assert {record["status"] for record in data["evidence_records"]} >= {
        "available", "confirmed_empty", "missing", "unavailable",
    }
    assert any(
        claim["fact_path"] == "profile.baseinfo.regCapital"
        and claim["value"] == "12000万"
        and claim["validation_status"] == "supported"
        for claim in result["answer"]["claims"]
    )
    assert any(
        claim["fact_path"] == "profile.holder_names"
        and claim["value"] == "甲投资控股有限公司"
        and claim["validation_status"] == "supported"
        for claim in result["answer"]["claims"]
    )
    holder_claim_ids = [
        claim["claim_id"]
        for claim in result["answer"]["claims"]
        if claim["fact_path"] == "profile.holder_names"
    ]
    assert len(holder_claim_ids) == 2
    assert len(set(holder_claim_ids)) == len(holder_claim_ids)
    assert any(
        claim["fact_path"] == "profile.baseinfo.established_date"
        and claim["value"] == "2024-01-01"
        and claim["validation_status"] == "supported"
        for claim in result["answer"]["claims"]
    )
    assert "注册资本：12000万" in result["answer"]["summary"]
    assert "股东：甲投资控股有限公司" in result["answer"]["summary"]
    assert "分支机构：明确无记录" in result["answer"]["summary"]
    assert "对外投资：查询失败" in result["answer"]["summary"]
    assert "历史变更：未查询" in result["answer"]["summary"]
    assert result["answer"]["status"] == "partial"
    assert "1704067200000" not in result["answer"]["summary"]
