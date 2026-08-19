"""Tests for multi-turn supplier context retention."""

import asyncio

from langchain_core.messages import HumanMessage

from app.graphs.context import build_input_messages
from app.services import agent
from app.services.agent import extract_supplier_references
from app.graphs.react_graph import _forced_external_access_call
from app.services.conversation_state import (
    build_conversation_state,
    resolve_supplier_target_selection,
    resolve_supplier_targets,
)


def test_extract_supplier_references_reads_nested_tool_results_and_deduplicates():
    result = extract_supplier_references(
        {
            "items": [
                {"supplier_name": "固安捷工业品"},
                {"supplier_name": "固安捷工业品"},
                {"company_name": "华东电机"},
            ]
        },
        "search_suppliers",
    )

    assert result == [
        {"name": "固安捷工业品", "kind": "supplier", "source": "search_suppliers"},
        {"name": "华东电机", "kind": "supplier", "source": "search_suppliers"},
    ]


def test_extract_supplier_references_preserves_external_contact_fields():
    result = extract_supplier_references(
        {
            "candidates": [{
                "supplier_name": "华东钢材供应有限公司",
                "source": "tianyancha_search",
                "website_url": "https://steel.example.com",
                "website_status": "unverified",
                "contact_phone": "021-12345678",
                "contact_email": "sales@steel.example.com",
                "contact_status": "unverified",
            }]
        },
        "discover_web_suppliers",
    )

    assert result == [{
        "name": "华东钢材供应有限公司",
        "kind": "supplier",
        "source": "discover_web_suppliers",
        "discovery_source": "tianyancha_search",
        "website_url": "https://steel.example.com",
        "website_status": "unverified",
        "contact_phone": "021-12345678",
        "contact_email": "sales@steel.example.com",
        "contact_status": "unverified",
    }]


def test_extract_supplier_references_preserves_external_candidate_identity():
    result = extract_supplier_references(
        {
            "candidates": [{
                "supplier_name": "深圳市云钥科技有限公司",
                "candidate_id": "candidate-cloud-key",
                "candidate_type": "external",
                "identity_status": "exact",
            }]
        },
        "search_suppliers",
    )

    assert result == [{
        "name": "深圳市云钥科技有限公司",
        "kind": "supplier",
        "source": "search_suppliers",
        "candidate_id": "candidate-cloud-key",
        "candidate_type": "external",
        "identity_status": "exact",
    }]


def test_extract_supplier_references_reads_company_names_from_markdown_answer():
    result = extract_supplier_references(
        "1. 深圳市立创电子有限公司\n2. 八方电气（苏州）股份有限公司",
        "Agent 回答",
    )

    assert [item["name"] for item in result] == [
        "深圳市立创电子有限公司",
        "八方电气（苏州）股份有限公司",
    ]


def test_extract_supplier_references_ignores_report_placeholders():
    result = extract_supplier_references(
        "两家公司、缺少目标公司、确认两家目标公司，待后续补充。",
        "Agent 回答",
    )

    assert result == []


def test_build_input_messages_includes_supplier_reference_context():
    messages = asyncio.run(
        build_input_messages(
            [{"role": "user", "content": "帮我找摄像头供应商"}],
            "它的风险怎么样？",
            [{"name": "固安捷工业品", "kind": "supplier"}],
        )
    )

    assert "固安捷工业品" in messages[0].content
    assert "这些企业" in messages[0].content
    assert messages[-1].content == "它的风险怎么样？"


def test_build_input_messages_routes_access_to_external_candidate_tool():
    messages = asyncio.run(
        build_input_messages(
            [],
            "对深圳市云钥科技有限公司执行准入申请",
            [{
                "name": "深圳市云钥科技有限公司",
                "candidate_id": "candidate-cloud-key",
                "candidate_type": "external",
                "identity_status": "exact",
            }],
        )
    )

    assert "candidate-cloud-key" in messages[0].content
    assert "必须立即调用 select_external_supplier_candidate" in messages[0].content
    assert "不得调用 select_sourcing_result" in messages[0].content


def test_react_graph_hard_routes_unambiguous_external_access_request():
    forced_call = _forced_external_access_call({
        "messages": [HumanMessage(content="对深圳市云钥科技有限公司执行准入申请")],
        "conversation_state": {"active_suppliers": [{
            "name": "深圳市云钥科技有限公司",
            "candidate_id": "candidate-cloud-key",
            "candidate_type": "external",
            "identity_status": "exact",
        }]},
    })

    assert forced_call is not None
    assert forced_call.tool_calls[0]["name"] == "select_external_supplier_candidate"
    assert forced_call.tool_calls[0]["args"]["candidate_id"] == "candidate-cloud-key"


def test_load_conversation_context_prefers_latest_structured_references(monkeypatch):
    class Conversations:
        def find_one(self, _query):
            return {
                "messages": [
                    {"role": "user", "content": "找电机供应商"},
                    {
                        "role": "assistant",
                        "content": "已找到候选供应商。",
                        "references": [
                            {"name": "甲电机有限公司", "kind": "supplier", "source": "search_suppliers"},
                            {"name": "乙电机有限公司", "kind": "supplier", "source": "search_suppliers"},
                        ],
                    },
                ],
                "references": [{"name": "历史供应商有限公司", "kind": "supplier"}],
            }

    monkeypatch.setattr(agent, "get_db", lambda: {"conversations": Conversations()})

    context = agent._load_conversation_context("context-test")

    assert [reference["name"] for reference in context["references"]] == [
        "甲电机有限公司", "乙电机有限公司",
    ]


def test_conversation_state_resolves_plural_supplier_analysis():
    references = [
        {"name": "甲电机有限公司", "kind": "supplier"},
        {"name": "乙电机有限公司", "kind": "supplier"},
    ]

    state = build_conversation_state("对这些企业做风险和舆情分析", references)

    assert state["selected_suppliers"] == ["甲电机有限公司", "乙电机有限公司"]
    assert state["current_task"]["analysis_dimensions"] == ["risk", "sentiment"]


def test_resolve_supplier_targets_honors_explicit_and_ordinal_references():
    references = [
        {"name": "甲电机有限公司"},
        {"name": "乙电机有限公司"},
        {"name": "丙电机有限公司"},
    ]

    assert resolve_supplier_targets("分析乙电机有限公司风险", references) == ["乙电机有限公司"]
    assert resolve_supplier_targets("评估前两家", references) == ["甲电机有限公司", "乙电机有限公司"]


def test_target_resolver_supports_aliases_exclusion_and_rank_filtering():
    references = [
        {"name": "甲电机有限公司", "aliases": ["甲电机"], "risk_level": "low"},
        {"name": "乙电机有限公司", "aliases": ["乙电机"], "risk_level": "medium"},
        {"name": "丙电机有限公司", "aliases": ["丙电机"], "risk_level": "low"},
    ]

    aliases = resolve_supplier_target_selection("对甲电机和丙电机做风险分析", references)
    excluded = resolve_supplier_target_selection("除了乙电机，其余企业做 ESG 分析", references)
    low_risk = resolve_supplier_target_selection("低风险的这些企业做舆情分析", references)
    low_risk_without_plural = resolve_supplier_target_selection("只看低风险的企业", references)
    first = resolve_supplier_target_selection("排名第一的企业做合规分析", references)

    assert aliases.target_supplier_names == ["甲电机有限公司", "丙电机有限公司"]
    assert excluded.target_supplier_names == ["甲电机有限公司", "丙电机有限公司"]
    assert low_risk.target_supplier_names == ["甲电机有限公司", "丙电机有限公司"]
    assert low_risk_without_plural.target_supplier_names == ["甲电机有限公司", "丙电机有限公司"]
    assert first.target_supplier_names == ["甲电机有限公司"]
    assert all(item.confidence >= 0.9 for item in [aliases, excluded, low_risk, low_risk_without_plural, first])


def test_target_resolver_requires_clarification_for_contextual_reference_without_suppliers():
    result = resolve_supplier_target_selection("对这些企业做风险评估", [])

    assert result.target_supplier_names == []
    assert result.needs_clarification is True
    assert result.confidence == 0.0
