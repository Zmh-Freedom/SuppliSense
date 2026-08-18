"""Shared ConversationState adapter for every chat execution graph."""

from __future__ import annotations

import json
from typing import Any

from app.graphs.agent_core.planner import plan_supplier_analysis_task
from app.services.conversation_state import build_conversation_state


def load_execution_context(session_id: str, user_message: str) -> dict[str, Any]:
    """Load durable conversation facts and resolve the current Agent task."""
    from app.services.agent import _load_conversation_context

    context = _load_conversation_context(session_id)
    return build_execution_context(
        session_id=session_id,
        user_message=user_message,
        history=context.get("history", []),
        references=context.get("references", []),
        previous_state=context.get("state", {}),
    )


def build_execution_context(
    *,
    session_id: str,
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    references: list[dict[str, Any]] | None = None,
    previous_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one serializable state and task view consumed by every graph."""
    normalized_history = list(history or [])
    normalized_references = [
        reference for reference in (references or []) if isinstance(reference, dict)
    ]
    conversation_state = build_conversation_state(
        user_message,
        normalized_references,
        previous_state,
        session_id=session_id,
    )
    current_task = dict(conversation_state.get("current_task") or {})
    targets = list(current_task.get("target_supplier_names") or [])
    dimensions = list(current_task.get("analysis_dimensions") or [])
    if current_task.get("task_type") == "analysis" and targets and dimensions:
        planned = plan_supplier_analysis_task(
            task_id=str(current_task.get("task_id") or "current-task"),
            supplier_names=targets,
            dimensions=dimensions,
        ).model_dump(mode="json")
        planned["user_message"] = user_message
        conversation_state["current_task"] = planned
        current_task = planned

    return {
        "session_id": session_id,
        "history": normalized_history,
        "references": normalized_references,
        "conversation_state": conversation_state,
        "current_task": current_task,
    }


def build_execution_prompt(execution_context: dict[str, Any]) -> str:
    """Render state facts as a non-ambiguous system prompt for every graph."""
    conversation_state = execution_context.get("conversation_state", {})
    task = execution_context.get("current_task", {})
    payload = {
        "active_suppliers": conversation_state.get("active_suppliers", []),
        "selected_supplier_names": conversation_state.get("selected_supplier_names", []),
        "current_task": task,
    }
    return (
        "结构化任务上下文（由系统解析，优先于代词猜测）：\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n必须针对 selected_supplier_names 中的全部供应商执行 current_task "
        "所列分析维度；没有目标时才向用户澄清。"
    )


def save_execution_turn(
    session_id: str,
    user_message: str,
    answer: str,
    references: list[dict[str, Any]] | None = None,
) -> None:
    """Persist every graph completion through the shared state-aware save path."""
    from app.services.agent import _save_turn

    _save_turn(session_id, user_message, answer, references or [])
