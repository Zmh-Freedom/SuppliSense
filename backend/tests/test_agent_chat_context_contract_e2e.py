"""P0 chat-entry regressions for the shared Agent execution context."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.api import chat as chat_api
from app.core.deps import get_current_user


pytestmark = [pytest.mark.agent_e2e, pytest.mark.agent_chat_contract]


@pytest.fixture
def authenticated_chat_client(client, app, monkeypatch: pytest.MonkeyPatch):
    """Exercise the protected HTTP route without requiring a seeded database user."""
    app.dependency_overrides[get_current_user] = lambda: object()
    monkeypatch.setattr(chat_api, "_optional_agent_user_id", lambda _request: "browser-user")
    yield client
    app.dependency_overrides.pop(get_current_user, None)


def _context(
    targets: list[str],
    dimensions: list[str],
    references: list[dict] | None = None,
) -> dict:
    task = {
        "task_id": "chat-contract-task",
        "task_type": "analysis",
        "target_supplier_names": targets,
        "analysis_dimensions": dimensions,
        "subtasks": [],
    }
    return {
        "history": [],
        "references": references or [],
        "conversation_state": {
            "selected_supplier_names": targets,
            "current_task": task,
        },
        "current_task": task,
        "llm_intent": {
            "target_supplier_names": targets,
            "analysis_dimensions": dimensions,
            "requested_action": "add_watchlist" if "监控" in dimensions else "none",
            "confidence": 1.0,
        },
    }


@pytest.mark.parametrize(
    ("message", "context", "expected_targets", "expected_dimensions"),
    [
        (
            "对四川建安工业有限责任公司做风险、ESG、舆情和合规分析，并加入监控",
            _context(
                ["四川建安工业有限责任公司"],
                ["risk", "esg", "sentiment", "compliance"],
            ),
            ["四川建安工业有限责任公司"],
            ["risk", "esg", "sentiment", "compliance"],
        ),
        (
            "对这两家做风险和舆情分析",
            _context(
                ["甲电机有限公司", "乙电机有限公司"],
                ["risk", "sentiment"],
                [{"name": "甲电机有限公司"}, {"name": "乙电机有限公司"}],
            ),
            ["甲电机有限公司", "乙电机有限公司"],
            ["risk", "sentiment"],
        ),
    ],
)
def test_authenticated_chat_stream_preserves_current_context_for_supervisor(
    authenticated_chat_client,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    context: dict,
    expected_targets: list[str],
    expected_dimensions: list[str],
) -> None:
    """The real protected endpoint must pass current-turn facts into its graph."""
    captured: dict = {}
    monkeypatch.setattr(
        "app.graphs.agent_core.adapter.load_execution_context",
        lambda *_args: context,
    )

    async def supervisor_stream(_sid, _message, _preferences, execution_context=None):
        captured["context"] = execution_context
        yield 'event: done\ndata: {"answer": "已完成"}\n\n'

    monkeypatch.setattr(chat_api, "_langgraph_agent_supervisor_stream", supervisor_stream)

    response = authenticated_chat_client.post(
        "/api/v1/chat/stream",
        json={"message": message, "session_id": str(uuid4()), "mode": "agent-supervisor"},
    )

    assert response.status_code == 200
    assert 'event: done\ndata: {"answer": "已完成"}' in response.text
    execution_context = captured["context"]
    assert execution_context["agent_user_id"]
    assert execution_context["current_task"]["target_supplier_names"] == expected_targets
    assert execution_context["current_task"]["analysis_dimensions"] == expected_dimensions


def test_chat_stream_rejects_anonymous_request(client) -> None:
    """The protected chat endpoint has no unauthenticated fallback branch."""
    response = client.post("/api/v1/chat/stream", json={"message": "分析风险"})

    assert response.status_code == 401


def test_chat_stream_fails_closed_when_llm_targets_are_lost(
    authenticated_chat_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dropped target must produce an error, never a misleading clarification."""
    invalid_context = _context(["四川建安工业有限责任公司"], ["risk"])
    invalid_context["current_task"] = {
        "task_id": "broken-task",
        "task_type": "analysis",
        "target_supplier_names": [],
        "analysis_dimensions": ["risk"],
    }
    monkeypatch.setattr(
        "app.graphs.agent_core.adapter.load_execution_context",
        lambda *_args: invalid_context,
    )

    response = authenticated_chat_client.post(
        "/api/v1/chat/stream",
        json={"message": "分析四川建安工业有限责任公司的风险", "mode": "agent-supervisor"},
    )

    assert response.status_code == 200
    assert "会话上下文校验失败，已停止执行" in response.text
    assert "缺少待评估供应商名称" not in response.text
