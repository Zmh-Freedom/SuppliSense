"""HTTP/SSE runner for the versioned Agent 60 acceptance fixture."""

from __future__ import annotations

import json
from collections.abc import Iterable
import os
from typing import Any
from uuid import uuid5, NAMESPACE_URL

from tests.evals.agent_60_fixture import load_agent_60_questions


def parse_sse_text(text: str) -> list[dict[str, Any]]:
    """Parse the small SSE contract used by the chat HTTP endpoint."""
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.removesuffix("\r")
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith("event: "):
            current["event"] = line[7:]
        elif line.startswith("id: "):
            current["id"] = int(line[4:])
        elif line.startswith("data: "):
            try:
                current["data"] = json.loads(line[6:])
            except json.JSONDecodeError:
                current["data"] = line[6:]
    if current:
        events.append(current)
    return events


def observed_terminal(events: Iterable[dict[str, Any]]) -> str:
    """Map an SSE trace to the public acceptance terminal state."""
    event_list = list(events)
    for event in reversed(event_list):
        event_type = event.get("event")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event_type == "done":
            answer = str(data.get("answer") or "")
            if any(token in answer for token in ("被拒绝", "拒绝", "未写入业务数据")):
                return "denied"
            return str(data.get("status") or "completed")
        if event_type == "approval_required":
            return "approval_required"
        if event_type == "clarification":
            return "clarification"
        if event_type == "error":
            return "error"
    return "no_terminal"


def terminal_matches(expected: str, observed: str, events: Iterable[dict[str, Any]]) -> bool:
    """Apply the stable, intentionally coarse terminal contract per question."""
    event_list = list(events)
    if expected == "client_validation":
        return True  # Q48 is validated by the browser component contract.
    if expected == "cancelled":
        return observed == "cancelled"
    if expected == "resumed":
        return observed in {"completed", "partial", "needs_review"}
    if expected == "forbidden":
        return any(
            any(token in str(event.get("data")) for token in ("权限", "禁止", "不在你当前负责范围", "无法执行采购复核"))
            for event in event_list
        )
    if expected == "forbidden_or_clarification":
        return observed in {"clarification", "error"} or any(
            "权限" in str(event.get("data")) or "禁止" in str(event.get("data"))
            for event in event_list
        )
    if expected == "approval_required":
        return observed == "approval_required" or (
            observed == "waiting_approval"
            and any(event.get("event") == "approval_required" for event in event_list)
        )
    if expected == "denied":
        return observed == "denied"
    if expected == "clarification":
        return observed == "clarification"
    if expected == "error":
        return observed == "error"
    if expected in {"completed", "needs_review", "partial"}:
        return observed in {expected, "completed", "partial", "needs_review"}
    if expected == "completed_or_partial":
        return observed in {"completed", "partial", "needs_review"}
    if expected == "idempotent":
        return observed in {"completed", "approval_required", "needs_review"}
    return observed == expected


def build_agent_60_http_cases(case_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Load and validate the execution contract before a live run starts."""
    questions = load_agent_60_questions()
    if case_ids is not None:
        questions = [question for question in questions if question["id"] in case_ids]
    if not questions:
        raise ValueError("Agent 60 live runner received no cases")
    return questions


def run_agent_60_http_cases(
    client: Any,
    access_token: str,
    *,
    marker: str,
    case_ids: set[str] | None = None,
    session_ids: dict[str, str] | None = None,
    access_tokens: dict[str, str] | None = None,
    assert_expected: bool = True,
) -> list[dict[str, Any]]:
    """Execute real chat HTTP/SSE requests and return an auditable trace."""
    results: list[dict[str, Any]] = []
    for case in build_agent_60_http_cases(case_ids):
        case_token = (access_tokens or {}).get(str(case.get("role") or ""), access_token)
        headers = {"Authorization": f"Bearer {case_token}"}
        session_id = (session_ids or {}).get(case["id"]) or str(uuid5(NAMESPACE_URL, f"agent60:{marker}:{case['session']}"))
        if case["expected_terminal"] == "client_validation":
            results.append({"id": case["id"], "status": "client_validation", "events": [], "passed": True})
            continue
        try:
            response = client.post(
                "/api/v1/chat/stream",
                json={"message": case["message"], "session_id": session_id, "mode": "auto"},
                headers=headers,
                timeout=float(os.getenv("AGENT_60_REQUEST_TIMEOUT_SECONDS", "30")),
            )
        except Exception as exc:
            result = {
                "id": case["id"],
                "session": case["session"],
                "http_status": 0,
                "observed_terminal": "request_error",
                "event_types": [],
                "error": str(exc),
                "passed": False,
            }
            results.append(result)
            if assert_expected:
                raise AssertionError(f"Agent 60 {case['id']} request failed: {result}") from exc
            continue
        events = parse_sse_text(response.text)
        observed = observed_terminal(events)
        result = {
            "id": case["id"],
            "session": case["session"],
            "http_status": response.status_code,
            "observed_terminal": observed,
            "event_types": [event.get("event") for event in events],
            "events": [
                {
                    "id": event.get("id"),
                    "event": event.get("event"),
                    "data": event.get("data"),
                }
                for event in events
            ],
            "passed": response.status_code == 200 and terminal_matches(case["expected_terminal"], observed, events),
        }
        results.append(result)
        if assert_expected and not result["passed"]:
            raise AssertionError(f"Agent 60 {case['id']} expected {case['expected_terminal']}, got {result}")
    return results


__all__ = [
    "build_agent_60_http_cases",
    "observed_terminal",
    "parse_sse_text",
    "run_agent_60_http_cases",
    "terminal_matches",
]
