"""Tests for the reusable, real-model sourcing chat acceptance contract."""

import json

import pytest

from app.evals.sourcing_chat_acceptance import (
    evaluate_sourcing_chat_case,
    load_sourcing_chat_cases,
    summarize_sourcing_chat_results,
)


def test_sourcing_chat_acceptance_set_has_execute_clarify_and_result_coverage() -> None:
    from pathlib import Path

    path = Path(__file__).parent / "evals" / "sourcing_chat_acceptance_cases.jsonl"
    cases = load_sourcing_chat_cases(path)

    assert len(cases) >= 5
    assert {case["expected"]["route"] for case in cases} == {"execute", "clarify"}
    assert {case["expected"]["candidate_result"] for case in cases} == {"found", "no_candidates"}
    assert {tag for case in cases for tag in case["tags"]} >= {
        "constraints", "ambiguity", "found_candidates", "no_candidates",
    }


def test_sourcing_chat_evaluator_counts_wrong_tools_clarifications_slots_and_answers() -> None:
    case = {
        "id": "SCA-example",
        "expected": {
            "route": "execute",
            "tool": "discover_supplier_candidates",
            "requirement": {"category": "工业相机", "region": "华东"},
            "candidate_result": "found",
            "answer_terms": ["已找到 2 家寻源候选"],
        },
    }

    result = evaluate_sourcing_chat_case(
        case,
        events=[
            {"event": "clarification", "data": {"message": "请补充预算"}},
            {"event": "tool_result", "data": {"result": {"data": {"candidates": []}}}},
            {"event": "agent_answer", "data": {"summary": "暂未识别到任务"}},
        ],
        tool_calls=[{"tool": "query_financials", "requirement": {"category": "工业相机"}}],
    )
    summary = summarize_sourcing_chat_results([result])

    assert result["passed"] is False
    assert summary["wrong_tool_call_cases"] == ["SCA-example"]
    assert summary["wrong_clarification_cases"] == ["SCA-example"]
    assert summary["constraint_mismatches"] == [{
        "id": "SCA-example",
        "fields": {"region": {"expected": "华东", "actual": None}},
    }]
    assert summary["irrelevant_answer_cases"] == ["SCA-example"]
    assert summary["candidate_result_mismatches"] == ["SCA-example"]


def test_sourcing_chat_evaluator_reports_unexpected_requirement_fields() -> None:
    case = {
        "id": "SCA-region-semantics",
        "expected": {
            "route": "execute",
            "requirement": {"supply_region": "苏州"},
            "requirement_absent_fields": ["region"],
        },
    }

    result = evaluate_sourcing_chat_case(
        case,
        events=[],
        tool_calls=[{
            "tool": "discover_supplier_candidates",
            "requirement": {"supply_region": "苏州", "region": "苏州"},
        }],
    )
    summary = summarize_sourcing_chat_results([result])

    assert result["mismatches"]["requirement"] == {
        "region": {"expected": "absent", "actual": "苏州"},
    }
    assert summary["constraint_mismatches"][0]["id"] == "SCA-region-semantics"


def test_sourcing_chat_evaluator_checks_semantic_answer_fragments() -> None:
    case = {
        "id": "SCA-answer-constraints",
        "expected": {
            "route": "execute",
            "answer_terms": [
                "预算：50万元",
                ["规格：", "-40", "125", ["℃", "摄氏度"]],
            ],
        },
    }

    result = evaluate_sourcing_chat_case(
        case,
        events=[{
            "event": "agent_answer",
            "data": {"summary": "按预算：50万元检索。规格：工作温度-40到125摄氏度。"},
        }],
        tool_calls=[{"tool": "discover_supplier_candidates", "requirement": {}}],
    )

    assert result["mismatches"] == {}


def test_sourcing_chat_evaluator_accepts_all_required_fragments_in_normalized_fields() -> None:
    case = {
        "id": "SCA-normalized-slots",
        "expected": {
            "route": "execute",
            "requirement": {
                "budget": ["含税", "2万元"],
                "specification": ["-40", "125", ["℃", "摄氏度"]],
            },
        },
    }

    result = evaluate_sourcing_chat_case(
        case,
        events=[],
        tool_calls=[{
            "tool": "discover_supplier_candidates",
            "requirement": {
                "budget": "含税不超过2万元",
                "specification": "工作温度 -40℃ 到 125℃",
            },
        }],
    )

    assert result["mismatches"] == {}


def test_sourcing_chat_evaluator_accepts_equivalent_unit_spellings() -> None:
    case = {
        "id": "SCA-equivalent-unit",
        "expected": {
            "route": "execute",
            "requirement": {"specification": ["-40", "125", ["℃", "摄氏度"]]},
        },
    }

    result = evaluate_sourcing_chat_case(
        case,
        events=[],
        tool_calls=[{
            "tool": "discover_supplier_candidates",
            "requirement": {"specification": "工作温度-40到125摄氏度"},
        }],
    )

    assert result["mismatches"] == {}


def test_sourcing_chat_case_loader_rejects_duplicate_ids(tmp_path) -> None:
    case = {
        "id": "duplicate",
        "message": "找供应商",
        "expected": {"route": "clarify"},
    }
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(case, ensure_ascii=False) for _ in range(2)), encoding="utf-8")

    with pytest.raises(ValueError, match="unique"):
        load_sourcing_chat_cases(path)
