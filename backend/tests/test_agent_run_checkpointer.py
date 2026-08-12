"""Lifecycle contracts for the Agent Run V2 LangGraph checkpointer."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.graphs.sourcing_risk_v2 import checkpointer


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
