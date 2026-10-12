"""Regression tests for the user input understanding evaluation set."""

import json

import pytest

from app.evals import agent_input_understanding


def test_input_understanding_eval_exposes_load_compare_and_run_contracts() -> None:
    assert callable(agent_input_understanding.load_input_understanding_cases)
    assert callable(agent_input_understanding.evaluate_input_understanding_case)
    assert callable(agent_input_understanding.run_input_understanding_evals)


def test_input_understanding_eval_loads_fixed_labeled_dialogues(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps({
            "id": "intent-risk",
            "tags": ["single_turn", "capability"],
            "conversation": [{"role": "user", "content": "查甲公司的风险"}],
            "context": {"references": []},
            "expected": {"intent": {"capability": "risk"}},
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    cases = agent_input_understanding.load_input_understanding_cases(path)

    assert cases[0]["id"] == "intent-risk"
    assert cases[0]["expected"]["intent"]["capability"] == "risk"


def test_input_understanding_eval_rejects_duplicate_ids_and_embedded_observations(tmp_path) -> None:
    case = {
        "id": "duplicate",
        "tags": ["single_turn", "capability"],
        "conversation": [{"role": "user", "content": "查甲公司的风险"}],
        "context": {},
        "expected": {"intent": {"capability": "risk"}},
    }
    path = tmp_path / "duplicate.jsonl"
    path.write_text("\n".join(json.dumps(case) for _ in range(2)), encoding="utf-8")

    with pytest.raises(ValueError, match="unique"):
        agent_input_understanding.load_input_understanding_cases(path)

    case["observed"] = {"intent": {"capability": "risk"}}
    path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="observed"):
        agent_input_understanding.load_input_understanding_cases(path)


def test_input_understanding_eval_reports_nested_field_and_tag_accuracy(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    cases = [
        {
            "id": "exact",
            "tags": ["single_turn", "capability"],
            "conversation": [{"role": "user", "content": "查甲公司的风险"}],
            "context": {},
            "expected": {"intent": {"capability": "risk"}, "decision": {"route": "execute"}},
        },
        {
            "id": "wrong-capability",
            "tags": ["single_turn", "ambiguity"],
            "conversation": [{"role": "user", "content": "看看乙公司"}],
            "context": {},
            "expected": {"intent": {"capability": "risk"}, "decision": {"route": "clarify"}},
        },
    ]
    path.write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in cases), encoding="utf-8")

    class FixedRunner:
        def run(self, case: dict) -> dict:
            if case["id"] == "exact":
                return {"intent": {"capability": "risk"}, "decision": {"route": "execute"}}
            return {"intent": {"capability": "financial"}, "decision": {"route": "clarify"}}

    report = agent_input_understanding.run_input_understanding_evals(path, FixedRunner())

    assert report["case_count"] == 2
    assert report["passed"] is False
    assert report["pass_rate"] == 0.5
    assert report["field_accuracy"]["intent.capability"] == {"correct": 1, "total": 2, "accuracy": 0.5}
    assert report["tag_accuracy"]["ambiguity"] == {"correct": 0, "total": 1, "accuracy": 0.0}
    assert report["cases"][1]["mismatches"] == {
        "intent.capability": {"expected": "risk", "actual": "financial"}
    }


def test_repository_input_understanding_corpus_has_required_coverage() -> None:
    from pathlib import Path

    path = Path(__file__).parent / "evals" / "input_understanding_cases.jsonl"

    cases = agent_input_understanding.load_input_understanding_cases(path)
    tags = {tag for case in cases for tag in case["tags"]}

    assert len(cases) >= 36
    assert {"single_turn", "multi_turn", "entity_resolution", "sourcing_constraints", "write_safety", "ambiguity"} <= tags
