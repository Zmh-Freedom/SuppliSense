"""Structured conversation facts shared by all Agent execution graphs."""

from __future__ import annotations

from typing import Any

from app.graphs.agent_core.contracts import AgentTask, ConversationState, migrate_conversation_state


_ANALYSIS_DIMENSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("risk", ("风险", "风险评估", "风险分析")),
    ("esg", ("ESG", "esg", "环境社会治理")),
    ("sentiment", ("舆情", "新闻", "负面信息")),
    ("compliance", ("合规", "制裁", "黑名单")),
)
_PLURAL_REFERENCE_TOKENS = (
    "这些企业", "上述企业", "这些供应商", "上述供应商",
    "推荐的供应商", "推荐企业", "它们", "全部企业", "所有企业",
)
_SINGULAR_REFERENCE_TOKENS = ("这家", "该企业", "该供应商", "它")
_ORDINAL_TARGETS = (("前两家", 2), ("前3家", 3), ("前三家", 3), ("前五家", 5))


def analysis_dimensions_from_message(message: str) -> list[str]:
    """Return explicitly requested analysis dimensions in stable order."""
    return [
        dimension
        for dimension, keywords in _ANALYSIS_DIMENSIONS
        if any(keyword in message for keyword in keywords)
    ]


def resolve_supplier_targets(
    message: str,
    supplier_references: list[dict[str, Any]],
) -> list[str]:
    """Resolve explicit and contextual supplier references without LLM guessing."""
    names = [
        str(reference.get("name", "")).strip()
        for reference in supplier_references
        if isinstance(reference, dict) and reference.get("name")
    ]
    names = list(dict.fromkeys(names))
    if not names:
        return []

    explicit_targets = [name for name in names if name in message]
    if explicit_targets:
        return explicit_targets
    for token, limit in _ORDINAL_TARGETS:
        if token in message:
            return names[:limit]
    if any(token in message for token in _PLURAL_REFERENCE_TOKENS):
        return names
    if any(token in message for token in _SINGULAR_REFERENCE_TOKENS):
        return names[:1]
    return []


def build_conversation_state(
    message: str,
    supplier_references: list[dict[str, Any]],
    previous_state: dict[str, Any] | None = None,
    *,
    session_id: str = "",
) -> dict[str, Any]:
    """Build the durable, serializable state for the current conversation turn."""
    previous = migrate_conversation_state(previous_state, session_id=session_id)
    active_suppliers = [
        reference
        for reference in supplier_references
        if isinstance(reference, dict) and reference.get("name")
    ]
    if not active_suppliers:
        active_suppliers = [reference.model_dump(mode="json") for reference in previous.active_suppliers]
    target_names = resolve_supplier_targets(message, active_suppliers)
    dimensions = analysis_dimensions_from_message(message)
    task_type = "analysis" if dimensions or target_names else "sourcing"
    state = ConversationState(
        session_id=session_id or previous.session_id,
        active_suppliers=active_suppliers,
        selected_supplier_names=target_names,
        current_requirement=previous.current_requirement,
        current_task=AgentTask(
            task_id="current-task",
            task_type=task_type,
            target_supplier_names=target_names,
            analysis_dimensions=dimensions,
            user_message=message,
        ),
        recent_tasks=previous.recent_tasks,
        pending_clarification=previous.pending_clarification,
        pending_approvals=previous.pending_approvals,
    )
    payload = state.model_dump(mode="json")
    payload["selected_suppliers"] = target_names
    return payload
