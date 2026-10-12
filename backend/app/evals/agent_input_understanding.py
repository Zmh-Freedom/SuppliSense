"""Fixed, service-free evaluation contracts for user input understanding."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Protocol


class AgentInputUnderstandingRunner(Protocol):
    """Run one fixed dialogue through an input-understanding implementation."""

    def run(self, case: dict[str, Any]) -> dict[str, Any]: ...


def load_input_understanding_cases(cases_path: str | Path) -> list[dict[str, Any]]:
    """Load JSONL cases and reject incomplete or contaminated fixtures."""
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(Path(cases_path).read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            case = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid input-understanding JSONL at line {line_number}") from exc
        if not isinstance(case, dict):
            raise ValueError(f"input-understanding case at line {line_number} must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"input-understanding case at line {line_number} requires an id")
        if case_id in seen_ids:
            raise ValueError(f"input-understanding case ids must be unique: {case_id}")
        seen_ids.add(case_id)
        tags = case.get("tags")
        if (
            not isinstance(tags, list)
            or not tags
            or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
        ):
            raise ValueError(f"input-understanding case {case_id} requires non-empty tags")
        conversation = case.get("conversation")
        if (
            not isinstance(conversation, list)
            or not conversation
            or not any(
                isinstance(turn, dict)
                and turn.get("role") == "user"
                and isinstance(turn.get("content"), str)
                and turn["content"].strip()
                for turn in conversation
            )
        ):
            raise ValueError(f"input-understanding case {case_id} requires a user dialogue turn")
        if not isinstance(case.get("context"), dict) or not isinstance(case.get("expected"), dict):
            raise ValueError(f"input-understanding case {case_id} requires context and expected objects")
        if "observed" in case:
            raise ValueError(f"input-understanding case {case_id} must not embed observed output")
        cases.append(case)
    if not cases:
        raise ValueError("input-understanding Eval must define at least one case")
    return cases


def _flatten_expected(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and value:
        flattened: dict[str, Any] = {}
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten_expected(nested, path))
        return flattened
    return {prefix: value}


def evaluate_input_understanding_case(
    case: dict[str, Any], observed: dict[str, Any]
) -> dict[str, Any]:
    """Compare only labeled fields, allowing implementations to return diagnostics."""
    expected = case.get("expected")
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        raise ValueError("case expectation and observed execution must be objects")
    expected_fields = _flatten_expected(expected)
    observed_fields = _flatten_expected(observed)
    mismatches = {
        field: {"expected": value, "actual": observed_fields.get(field)}
        for field, value in expected_fields.items()
        if observed_fields.get(field) != value
    }
    return {
        "id": case["id"],
        "tags": list(case.get("tags") or []),
        "passed": not mismatches,
        "mismatches": mismatches,
    }


def _accuracy(correct: int, total: int) -> dict[str, int | float]:
    return {
        "correct": correct,
        "total": total,
        "accuracy": round(correct / total, 4) if total else 0.0,
    }


def run_input_understanding_evals(
    cases_path: str | Path,
    runner: AgentInputUnderstandingRunner,
) -> dict[str, Any]:
    """Execute cases through an injected parser and report field/tag-level accuracy."""
    cases = load_input_understanding_cases(cases_path)
    results: list[dict[str, Any]] = []
    field_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    tag_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    for case in cases:
        observed = runner.run(case)
        result = evaluate_input_understanding_case(case, observed)
        results.append(result)
        flattened_expected = _flatten_expected(case["expected"])
        flattened_observed = _flatten_expected(observed)
        for field, expected_value in flattened_expected.items():
            field_totals[field][1] += 1
            field_totals[field][0] += int(flattened_observed.get(field) == expected_value)
        for tag in case["tags"]:
            tag_totals[tag][1] += 1
            tag_totals[tag][0] += int(result["passed"])

    passed_count = sum(int(result["passed"]) for result in results)
    return {
        "eval_version": "input-understanding-v1",
        "case_count": len(results),
        "passed": passed_count == len(results),
        "pass_rate": round(passed_count / len(results), 4),
        "field_accuracy": {
            field: _accuracy(correct, total)
            for field, (correct, total) in sorted(field_totals.items())
        },
        "tag_accuracy": {
            tag: _accuracy(correct, total)
            for tag, (correct, total) in sorted(tag_totals.items())
        },
        "cases": results,
    }


__all__ = [
    "AgentInputUnderstandingRunner",
    "evaluate_input_understanding_case",
    "load_input_understanding_cases",
    "run_input_understanding_evals",
]
