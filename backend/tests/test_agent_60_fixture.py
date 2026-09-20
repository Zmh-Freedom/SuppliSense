"""Schema and isolation checks for the 60-question acceptance fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evals.agent_60_fixture import (
    build_agent_60_recovery_fixture,
    load_agent_60_manifest,
    load_agent_60_questions,
)
from tests.evals.agent_60_live_runner import (
    build_agent_60_http_cases,
    observed_terminal,
    parse_sse_text,
    run_agent_60_http_cases,
    terminal_matches,
)

EVAL_DIR = Path(__file__).parent / "evals"
QUESTIONS_PATH = EVAL_DIR / "agent_60_questions.jsonl"
MANIFEST_PATH = EVAL_DIR / "agent_60_fixture_manifest.json"


def _load_questions() -> list[dict]:
    return load_agent_60_questions()


def test_agent_60_fixture_has_all_questions_and_no_duplicate_ids() -> None:
    questions = _load_questions()
    assert len(questions) == 60
    assert [item["id"] for item in questions] == [f"Q{index:02d}" for index in range(1, 61)]
    assert len({item["session"] for item in questions}) >= 20


def test_agent_60_fixture_has_explicit_execution_contract() -> None:
    questions = _load_questions()
    required = {"id", "session", "role", "message", "capability", "write", "priority", "preconditions", "expected_terminal"}
    assert all(required <= set(item) for item in questions)
    assert {item["priority"] for item in questions} == {"P0", "P1", "P2"}
    assert any(item["write"] for item in questions)
    assert any(not item["write"] for item in questions)


def test_agent_60_fixture_manifest_declares_isolation_and_all_preconditions() -> None:
    manifest = load_agent_60_manifest()
    questions = _load_questions()
    required_entities = set(manifest["required_entities"])
    referenced = {
        precondition
        for question in questions
        for precondition in question["preconditions"]
        if not precondition.startswith("Q")
    }
    assert manifest["namespace"] == "E2E_TEST"
    assert manifest["cleanup_prefix"] == "agent60_"
    assert referenced <= required_entities
    assert required_entities <= set(manifest["entities"])


@pytest.mark.parametrize("path", [QUESTIONS_PATH, MANIFEST_PATH])
def test_agent_60_fixture_files_are_utf8_and_valid_json(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        json.loads(text)
    else:
        for line in text.splitlines():
            if line.strip():
                json.loads(line)


@pytest.mark.parametrize("kind", ["paused_review_run", "paused_identity_run", "refreshable_run"])
def test_agent_60_recovery_fixtures_are_isolated_and_resumable(kind: str) -> None:
    fixture = build_agent_60_recovery_fixture(kind)

    assert fixture["namespace"] == "E2E_TEST"
    assert fixture["cleanup"] is True
    assert fixture["session_id"] == "agent60-session"
    assert fixture["run_id"].startswith("agent60_")
    assert fixture["resume_command"]


def test_agent_60_live_runner_parses_terminal_sse_contract() -> None:
    events = parse_sse_text(
        'id: 4\nevent: workflow_status\ndata: {"status":"completed"}\n\n'
        'event: done\ndata: {"answer":"完成","status":"completed"}\n\n'
    )
    assert observed_terminal(events) == "completed"
    assert terminal_matches("completed", "completed", events)
    assert len(build_agent_60_http_cases({"Q46", "Q60"})) == 2


@pytest.mark.parametrize(
    ("expected", "observed"),
    [("forbidden_or_clarification", "clarification"), ("completed_or_partial", "partial"), ("idempotent", "completed")],
)
def test_agent_60_live_runner_acceptance_aliases(expected: str, observed: str) -> None:
    assert terminal_matches(expected, observed, [{"event": "done", "data": {"status": observed}}])


def test_agent_60_live_runner_executes_a_real_http_client_contract() -> None:
    class Response:
        status_code = 200
        text = 'event: done\ndata: {"answer":"完成","status":"completed"}\n\n'

    class Client:
        def post(self, path, *, json, headers, timeout=None):
            assert path == "/api/v1/chat/stream"
            assert json["session_id"]
            assert headers["Authorization"].startswith("Bearer ")
            return Response()

    results = run_agent_60_http_cases(
        Client(),
        "token",
        marker="fixture",
        case_ids={"Q48", "Q49"},
        assert_expected=False,
    )
    assert [result["id"] for result in results] == ["Q48", "Q49"]
