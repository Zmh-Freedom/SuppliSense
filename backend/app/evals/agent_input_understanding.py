"""Fixed, service-free evaluation contracts for user input understanding."""

from __future__ import annotations

import json
import argparse
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
        "observed": observed,
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


def _last_user_turn(case: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Return the latest user input and all preceding dialogue turns."""
    conversation = case["conversation"]
    user_index = max(
        index for index, turn in enumerate(conversation)
        if turn.get("role") == "user" and str(turn.get("content") or "").strip()
    )
    message = str(conversation[user_index]["content"]).strip()
    history = [turn for turn in conversation[:user_index] if isinstance(turn, dict)]
    return message, history


def _evaluation_decision(
    message: str,
    intent: dict[str, Any],
    current_task: dict[str, Any],
    conversation_state: dict[str, Any],
) -> dict[str, Any]:
    """Mirror the local execute/clarify/approval decision after parsing.

    Database-backed supplier scope, identity search, and provider preflights
    are excluded from this service-free evaluation.
    """
    from app.graphs.agent_core.intent_extractor import explicit_write_action

    explicit_action = explicit_write_action(message)
    action = explicit_action if explicit_action != "none" else intent.get("requested_action") or "none"
    targets = intent.get("target_supplier_names") or current_task.get("target_supplier_names") or []
    entity_resolution = conversation_state.get("entity_resolution") or {}
    if action != "none":
        if action != "batch_add_watchlist" and not targets:
            return {"route": "clarify", "missing_fields": ["supplier"]}
        return {"route": "approval", "missing_fields": []}

    if entity_resolution.get("status") == "ambiguous":
        return {"route": "clarify", "missing_fields": ["supplier_identity"]}

    if intent.get("needs_clarification"):
        missing_fields = list(intent.get("missing_fields") or [])
        if "target_supplier_names" in missing_fields:
            missing_fields = ["supplier"]
        elif "sourcing_requirement" in missing_fields:
            requirement = current_task.get("requirement") or intent.get("sourcing_requirement") or {}
            category = str(requirement.get("category") or "").strip()
            missing_fields = [
                "product_or_category"
                if not category or category in {"一家合适", "合适的供应商", "供应商", "产品", "合适产品"}
                else "specification"
            ]
        return {"route": "clarify", "missing_fields": missing_fields}

    if intent.get("task_type") == "none" or intent.get("capability") == "none":
        return {"route": "respond", "missing_fields": []}
    if intent.get("task_type") == "sourcing":
        requirement = current_task.get("requirement") or intent.get("sourcing_requirement") or {}
        missing = current_task.get("requirement_missing") or []
        if not requirement.get("category") and "category" not in missing:
            missing = [*missing, "category"]
        if missing:
            return {
                "route": "clarify",
                "missing_fields": [
                    "product_or_category" if field == "category" else "specification"
                    for field in missing
                ],
            }
        return {"route": "execute", "missing_fields": []}
    if not targets and intent.get("scope") not in {
        "supplier_group", "responsible_suppliers", "supplier_directory",
    }:
        return {"route": "clarify", "missing_fields": ["supplier"]}
    return {"route": "execute", "missing_fields": []}


def run_production_input_understanding_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one dialogue through production extraction and context overlay."""
    from app.graphs.agent_core import adapter
    from app.graphs.agent_core.intent_extractor import extract_conversation_intent

    message, history = _last_user_turn(case)
    fixture_context = case.get("context") if isinstance(case.get("context"), dict) else {}
    references = fixture_context.get("references")
    references = references if isinstance(references, list) else []
    previous_state = fixture_context.get("conversation_state")
    previous_state = previous_state if isinstance(previous_state, dict) else {}
    session_id = f"input-understanding-eval:{case['id']}"

    execution_context = adapter.build_execution_context(
        session_id=session_id,
        user_message=message,
        history=history,
        references=references,
        previous_state=previous_state,
    )
    extracted = extract_conversation_intent(
        message,
        execution_context["references"],
        conversation_context={
            "session_id": session_id,
            "history": execution_context["history"],
            "conversation_state": previous_state,
        },
    )
    resolved = adapter.apply_extracted_conversation_intent(execution_context, extracted)
    resolved = adapter._enforce_scope_query_intent(resolved, message)
    resolved = adapter._apply_identity_verification_intent(resolved, message)
    resolved = adapter._apply_harness_sourcing_requirement(resolved, message)
    resolved = adapter._apply_sourcing_follow_up(resolved, message)
    resolved = adapter._persist_input_understanding(
        resolved,
        user_message=message,
        previous_state=previous_state,
    )

    intent = dict(resolved.get("llm_intent") or {})
    persisted = (resolved.get("conversation_state") or {}).get("input_understanding")
    if isinstance(persisted, dict):
        intent = {**intent, **persisted}
    task = dict(resolved.get("current_task") or {})
    requirement = task.get("requirement") or intent.get("sourcing_requirement")
    if isinstance(requirement, dict):
        intent["sourcing_requirement"] = requirement
    elif intent.get("sourcing_requirement") is None:
        intent["sourcing_requirement"] = None
    executable_task_type = task.get("task_type")
    if executable_task_type:
        intent["task_type"] = executable_task_type

    output: dict[str, Any] = {
        "intent": {
            field: intent.get(field)
            for field in (
                "task_type", "capability", "target_supplier_names", "analysis_dimensions",
                "scope", "requested_action", "sourcing_requirement", "time_range",
            )
        },
        "field_sources": dict(intent.get("field_sources") or {}),
        "decision": _evaluation_decision(
            message, intent, task, dict(resolved.get("conversation_state") or {}),
        ),
        "diagnostics": {
            key: intent.get(key)
            for key in (
                "interpretation_summary", "confidence", "ambiguities", "needs_clarification",
                "missing_fields", "clarification_question",
            )
        },
        "execution_task": {
            key: task[key]
            for key in (
                "task_type", "capability", "scope", "target_supplier_names",
                "analysis_dimensions", "requirement", "requirement_status",
                "requirement_missing",
            )
            if key in task
        },
    }
    entity_resolution = (resolved.get("conversation_state") or {}).get("entity_resolution")
    if isinstance(entity_resolution, dict):
        output["entity_resolution"] = entity_resolution
    return output


def run_production_input_understanding_evals(cases_path: str | Path) -> dict[str, Any]:
    """Run the fixed corpus through the current production parser."""
    class ProductionRunner:
        def run(self, case: dict[str, Any]) -> dict[str, Any]:
            return run_production_input_understanding_case(case)

    report = run_input_understanding_evals(cases_path, ProductionRunner())
    report["runner"] = "production_harness_parse"
    report["scope"] = "context_building+llm_extraction+normalization+overlay+local_decision"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run input cases through the production parser")
    parser.add_argument(
        "--cases",
        default=str(Path(__file__).parents[2] / "tests" / "evals" / "input_understanding_cases.jsonl"),
        help="JSONL cases (defaults to the fixed repository corpus)",
    )
    parser.add_argument("--output", help="Write the JSON report to this path; stdout when omitted")
    args = parser.parse_args()

    from app.core.config import settings

    if not settings.LLM_API_KEY:
        parser.error("LLM_API_KEY is not configured; refusing to report fallback as a real-model eval")
    report = run_production_input_understanding_evals(args.cases)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote {report['case_count']} cases to {output_path}")
    else:
        print(rendered)


__all__ = [
    "AgentInputUnderstandingRunner",
    "evaluate_input_understanding_case",
    "load_input_understanding_cases",
    "run_production_input_understanding_case",
    "run_production_input_understanding_evals",
    "run_input_understanding_evals",
]


if __name__ == "__main__":
    main()
