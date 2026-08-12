"""Persistent LangGraph checkpoint lifecycle for Sourcing Risk Agent V2."""

from __future__ import annotations

import re
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

from app.core.config import settings

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
except ImportError:  # pragma: no cover - exercised in deployments missing the optional package
    AsyncPostgresSaver = None  # type: ignore[assignment,misc]


_checkpointer: AsyncPostgresSaver | None = None
_checkpointer_context: AbstractAsyncContextManager[AsyncPostgresSaver] | None = None
_SCHEMA_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _connection_string() -> str:
    """Build a dedicated psycopg3 URI without exposing raw credentials."""
    user = quote(settings.PG_USER, safe="")
    password = quote(settings.PG_PASSWORD, safe="")
    database = quote(settings.PG_DB, safe="")
    options = urlencode({"options": f"-csearch_path={settings.AGENT_RUN_CHECKPOINT_SCHEMA}"})
    return f"postgresql://{user}:{password}@{settings.PG_HOST}:{settings.PG_PORT}/{database}?{options}"


async def _new_checkpointer() -> AsyncPostgresSaver:
    """Open an independent psycopg3 saver; it never uses the psycopg2 pool."""
    global _checkpointer_context

    if AsyncPostgresSaver is None:
        raise RuntimeError(
            "LangGraph PostgreSQL checkpoint support is unavailable; "
            "install langgraph-checkpoint-postgres and psycopg[binary]."
        )

    context = AsyncPostgresSaver.from_conn_string(_connection_string())
    saver = await context.__aenter__()
    _checkpointer_context = context
    return saver


async def _ensure_checkpoint_schema(saver: AsyncPostgresSaver) -> None:
    """Create the isolated checkpoint schema through the saver connection."""
    schema = settings.AGENT_RUN_CHECKPOINT_SCHEMA
    if not _SCHEMA_NAME_PATTERN.fullmatch(schema):
        raise ValueError("AGENT_RUN_CHECKPOINT_SCHEMA 必须是有效的 PostgreSQL 标识符")
    await saver.conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


async def get_sourcing_risk_checkpointer() -> AsyncPostgresSaver:
    """Return the process-wide, initialized persistent checkpoint saver."""
    global _checkpointer

    if _checkpointer is not None:
        return _checkpointer

    saver = await _new_checkpointer()
    try:
        await _ensure_checkpoint_schema(saver)
        await saver.setup()
    except Exception:
        await close_sourcing_risk_checkpointer()
        raise

    _checkpointer = saver
    return saver


def is_sourcing_risk_checkpointer_ready() -> bool:
    """Report whether the persistent saver completed setup in this process."""
    return _checkpointer is not None


async def close_sourcing_risk_checkpointer() -> None:
    """Release the dedicated psycopg3 connection held by the saver."""
    global _checkpointer, _checkpointer_context

    context = _checkpointer_context
    _checkpointer = None
    _checkpointer_context = None
    if context is not None:
        await context.__aexit__(None, None, None)
