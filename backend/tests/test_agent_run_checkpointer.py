"""Lifecycle contracts for the Agent Run V2 LangGraph checkpointer."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.graphs.sourcing_risk_v2 import checkpointer
from app.graphs.agents import sourcing


@pytest.fixture(autouse=True)
def reset_checkpointer() -> None:
    """Keep the module-level lifecycle state isolated between contracts."""
    checkpointer._checkpointer = None
    checkpointer._checkpointer_context = None


def test_checkpointer_setup_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated retrieval must reuse one configured persistent saver."""
    fake = AsyncMock()
    monkeypatch.setattr(checkpointer, "_new_checkpointer", AsyncMock(return_value=fake))

    assert asyncio.run(checkpointer.get_sourcing_risk_checkpointer()) is fake
    assert asyncio.run(checkpointer.get_sourcing_risk_checkpointer()) is fake
    fake.conn.execute.assert_awaited_once_with('CREATE SCHEMA IF NOT EXISTS "agent_checkpoint"')
    fake.setup.assert_awaited_once()


def test_checkpointer_setup_failure_does_not_cache_unavailable_saver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed database setup must be retried rather than reported as healthy."""
    fake = AsyncMock()
    fake.setup.side_effect = RuntimeError("checkpoint database unavailable")
    monkeypatch.setattr(checkpointer, "_new_checkpointer", AsyncMock(return_value=fake))

    with pytest.raises(RuntimeError, match="checkpoint database unavailable"):
        asyncio.run(checkpointer.get_sourcing_risk_checkpointer())

    assert checkpointer._checkpointer is None


def test_close_checkpointer_releases_context_and_allows_reinitialization() -> None:
    """A shutdown must release the saver context before a later lifespan starts."""
    context = AsyncMock()
    saver = AsyncMock()
    checkpointer._checkpointer = saver
    checkpointer._checkpointer_context = context

    asyncio.run(checkpointer.close_sourcing_risk_checkpointer())

    context.__aexit__.assert_awaited_once_with(None, None, None)
    assert checkpointer._checkpointer is None
    assert checkpointer._checkpointer_context is None


def test_compile_graph_binds_initialized_saver(monkeypatch: pytest.MonkeyPatch) -> None:
    """V2 graph compilation must persist checkpoints through the initialized saver."""
    graph = Mock()
    saver = object()
    monkeypatch.setattr(checkpointer.settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(checkpointer, "is_sourcing_risk_checkpointer_ready", lambda: True)
    monkeypatch.setattr(checkpointer, "_checkpointer", saver)

    result = checkpointer.compile_graph(graph)

    assert result is graph.compile.return_value
    graph.compile.assert_called_once_with(checkpointer=saver)


def test_compile_graph_omits_checkpointer_when_v2_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feature-off compilation must be explicit and never open a database saver."""
    graph = Mock()
    monkeypatch.setattr(checkpointer.settings, "AGENT_RUN_V2_ENABLED", False)

    result = checkpointer.compile_sourcing_risk_graph(graph)

    assert result is graph.compile.return_value
    graph.compile.assert_called_once_with(checkpointer=None)


def test_build_sourcing_graph_binds_explicit_checkpoint_saver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real sourcing graph entry point must compile with its supplied saver."""
    graph = Mock()
    saver = object()
    monkeypatch.setattr(sourcing, "StateGraph", Mock(return_value=graph))
    monkeypatch.setattr(checkpointer.settings, "AGENT_RUN_V2_ENABLED", True)

    result = sourcing.build_sourcing_graph(checkpointer=saver)

    assert result is graph.compile.return_value
    graph.compile.assert_called_once_with(checkpointer=saver)


def test_build_sourcing_graph_binds_initialized_default_saver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real entry point must use the lifespan-initialized saver by default."""
    graph = Mock()
    saver = object()
    monkeypatch.setattr(sourcing, "StateGraph", Mock(return_value=graph))
    monkeypatch.setattr(checkpointer.settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(checkpointer, "_checkpointer", saver)

    result = sourcing.build_sourcing_graph()

    assert result is graph.compile.return_value
    graph.compile.assert_called_once_with(checkpointer=saver)


def test_resume_runner_uses_persistent_saver_and_identity_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing the saver or resume payload would prevent cross-process identity recovery."""
    from app.graphs.sourcing_risk_v2 import runner

    saver = object()
    graph = AsyncMock()
    monkeypatch.setattr(runner, "get_sourcing_risk_checkpointer", AsyncMock(return_value=saver))
    monkeypatch.setattr(runner, "build_sourcing_risk_graph", Mock(return_value=graph))

    asyncio.run(runner._resume("run-id", {"identity_resolutions": {"candidate-a": "company-a"}}))

    runner.build_sourcing_risk_graph.assert_called_once_with(saver)
    command, config = graph.ainvoke.await_args.args
    assert command.resume == {"identity_resolutions": {"candidate-a": "company-a"}}
    assert config == {"configurable": {"thread_id": "run-id"}}
