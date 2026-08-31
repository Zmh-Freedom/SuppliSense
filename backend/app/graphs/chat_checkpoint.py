"""Checkpoint configuration for chat graphs sharing one PostgreSQL saver."""

from __future__ import annotations


def chat_checkpoint_config(session_id: str, graph_name: str) -> dict[str, dict[str, str]]:
    """Isolate each chat graph's durable state within the same user session."""
    return {
        "configurable": {
            "thread_id": session_id,
            "checkpoint_ns": f"chat:{graph_name}",
        }
    }
