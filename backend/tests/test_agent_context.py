"""Tests for multi-turn supplier context retention."""

import asyncio

from app.graphs.context import build_input_messages
from app.services.agent import extract_supplier_references


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
    assert messages[-1].content == "它的风险怎么样？"
