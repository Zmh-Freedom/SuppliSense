"""Evaluation contracts for real-model sourcing requests over chat SSE."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_sourcing_chat_cases(path: str | Path) -> list[dict[str, Any]]:
    """Load labeled sourcing chat cases from JSONL and reject duplicate IDs."""
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            case = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid sourcing chat JSONL at line {line_number}") from exc
        if not isinstance(case, dict):
            raise ValueError(f"sourcing chat case at line {line_number} must be an object")
        case_id = case.get("id")
        expected = case.get("expected")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"sourcing chat case at line {line_number} requires an id")
        if case_id in seen_ids:
            raise ValueError(f"sourcing chat case ids must be unique: {case_id}")
        seen_ids.add(case_id)
        if not isinstance(case.get("message"), str) or not case["message"].strip():
            raise ValueError(f"sourcing chat case {case_id} requires a message")
        if not isinstance(expected, dict) or expected.get("route") not in {"execute", "clarify"}:
            raise ValueError(f"sourcing chat case {case_id} requires an execute/clarify expectation")
        if expected["route"] == "execute" and not isinstance(expected.get("requirement"), dict):
            raise ValueError(f"sourcing chat case {case_id} requires requirement expectations")
        cases.append(case)
    if not cases:
        raise ValueError("sourcing chat acceptance set must define at least one case")
    return cases


def _contains_expected_value(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.casefold() in actual.casefold()
    if (
        isinstance(expected, list)
        and expected
        and all(
            isinstance(item, str)
            or (isinstance(item, list) and item and all(isinstance(option, str) for option in item))
            for item in expected
        )
        and isinstance(actual, str)
    ):
        normalized_actual = actual.casefold()
        return all(
            any(option.casefold() in normalized_actual for option in item)
            if isinstance(item, list)
            else item.casefold() in normalized_actual
            for item in expected
        )
    return expected == actual


def evaluate_sourcing_chat_case(
    case: dict[str, Any],
    *,
    events: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare live SSE observations with routing, slot, result, and answer labels."""
    expected = case["expected"]
    event_types = [str(event.get("event") or "") for event in events]
    clarifications = [
        event.get("data") if isinstance(event.get("data"), dict) else {}
        for event in events
        if event.get("event") == "clarification"
    ]
    answers = [
        event.get("data") if isinstance(event.get("data"), dict) else {}
        for event in events
        if event.get("event") == "agent_answer"
    ]
    tool_results = [
        event.get("data") if isinstance(event.get("data"), dict) else {}
        for event in events
        if event.get("event") == "tool_result"
    ]
    mismatches: dict[str, Any] = {}

    if expected["route"] == "execute":
        expected_tool = str(expected.get("tool") or "discover_supplier_candidates")
        if clarifications:
            mismatches["route"] = {"expected": "execute", "actual": "clarify"}
        if len(tool_calls) != 1 or str(tool_calls[0].get("tool") or "") != expected_tool:
            mismatches["tool_call"] = {
                "expected": {"count": 1, "tool": expected_tool},
                "actual": tool_calls,
            }
        requirement = tool_calls[0].get("requirement") if tool_calls else None
        requirement = requirement if isinstance(requirement, dict) else {}
        field_mismatches = {
            field: {"expected": value, "actual": requirement.get(field)}
            for field, value in expected.get("requirement", {}).items()
            if not _contains_expected_value(value, requirement.get(field))
        }
        for field in expected.get("requirement_absent_fields", []):
            if requirement.get(field) not in (None, "", [], {}):
                field_mismatches[str(field)] = {
                    "expected": "absent",
                    "actual": requirement.get(field),
                }
        if field_mismatches:
            mismatches["requirement"] = field_mismatches
        answer_summary = str((answers[-1] if answers else {}).get("summary") or "")
        missing_answer_terms = [
            term for term in expected.get("answer_terms", [])
            if not _contains_expected_value(term, answer_summary)
        ]
        if missing_answer_terms:
            mismatches["answer_relevance"] = {
                "missing_terms": missing_answer_terms,
                "summary": answer_summary,
            }
        expected_result = expected.get("candidate_result")
        if expected_result:
            result_data = (tool_results[-1].get("result") or {}).get("data") or {} if tool_results else {}
            candidate_count = len(result_data.get("candidates") or [])
            observed_result = "found" if candidate_count else "no_candidates"
            if observed_result != expected_result:
                mismatches["candidate_result"] = {
                    "expected": expected_result,
                    "actual": observed_result,
                }
    else:
        if not clarifications:
            mismatches["route"] = {"expected": "clarify", "actual": "execute_or_respond"}
        if tool_calls:
            mismatches["tool_call"] = {"expected": [], "actual": tool_calls}
        clarification_text = " ".join(
            str(item.get(key) or "")
            for item in clarifications
            for key in ("message", "question", "clarification_question")
        )
        expected_terms = expected.get("missing_fields", [])
        if expected_terms and not any(str(term).casefold() in clarification_text.casefold() for term in expected_terms):
            mismatches["clarification_relevance"] = {
                "expected_terms": expected_terms,
                "actual": clarification_text,
            }

    return {
        "id": case["id"],
        "route": expected["route"],
        "passed": not mismatches,
        "mismatches": mismatches,
        "event_types": event_types,
    }


def summarize_sourcing_chat_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Report the acceptance metrics that catch routing and answer regressions."""
    wrong_tool_cases = [item["id"] for item in results if "tool_call" in item["mismatches"]]
    wrong_clarification_cases = [
        item["id"] for item in results
        if "route" in item["mismatches"] or "clarification_relevance" in item["mismatches"]
    ]
    constraint_mismatches = [
        {"id": item["id"], "fields": item["mismatches"]["requirement"]}
        for item in results if "requirement" in item["mismatches"]
    ]
    irrelevant_answer_cases = [
        item["id"] for item in results if "answer_relevance" in item["mismatches"]
    ]
    candidate_result_mismatches = [
        item["id"] for item in results if "candidate_result" in item["mismatches"]
    ]
    passed_count = sum(bool(item["passed"]) for item in results)
    total = len(results)
    return {
        "case_count": total,
        "passed_count": passed_count,
        "pass_rate": round(passed_count / total, 4) if total else 0.0,
        "passed": total > 0 and passed_count == total,
        "wrong_tool_call_cases": wrong_tool_cases,
        "wrong_clarification_cases": wrong_clarification_cases,
        "constraint_mismatches": constraint_mismatches,
        "irrelevant_answer_cases": irrelevant_answer_cases,
        "candidate_result_mismatches": candidate_result_mismatches,
        "cases": results,
    }


__all__ = [
    "evaluate_sourcing_chat_case",
    "load_sourcing_chat_cases",
    "summarize_sourcing_chat_results",
]
