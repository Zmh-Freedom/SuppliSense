"""Shared ConversationState adapter for every chat execution graph."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.logging import get_logger
from app.graphs.agent_core.entity_memory import memory_from_state, resolve_turn
from app.graphs.agent_core.planner import plan_supplier_analysis_task
from app.services.conversation_state import build_conversation_state

logger = get_logger()


class ExecutionContextContractViolation(RuntimeError):
    """Raised when a chat graph would lose the shared execution context."""


class ExecutionContextContract(BaseModel):
    """Minimal, version-tolerant shape required by every chat execution graph."""

    model_config = ConfigDict(extra="allow")

    session_id: str = ""
    history: list[dict[str, Any]] = Field(default_factory=list)
    references: list[dict[str, Any]] = Field(default_factory=list)
    conversation_state: dict[str, Any] = Field(default_factory=dict)
    current_task: dict[str, Any] = Field(default_factory=dict)


def validate_execution_context(
    execution_context: dict[str, Any], *, source: str
) -> dict[str, Any]:
    """Fail closed when a graph input drops LLM-resolved task facts."""
    try:
        contract = ExecutionContextContract.model_validate(execution_context)
    except ValidationError as exc:
        _raise_context_contract_violation(source, "invalid_execution_context", str(exc))

    current_task = contract.current_task
    llm_intent = execution_context.get("llm_intent")
    llm_intent = llm_intent if isinstance(llm_intent, dict) else {}
    expected_targets = _text_list(llm_intent.get("target_supplier_names"))
    expected_dimensions = _text_list(llm_intent.get("analysis_dimensions"))
    actual_targets = _text_list(current_task.get("target_supplier_names"))
    actual_dimensions = _text_list(current_task.get("analysis_dimensions"))
    if expected_targets and actual_targets != expected_targets:
        _raise_context_contract_violation(
            source,
            "llm_targets_not_preserved",
            "LLM 解析目标未完整进入 current_task",
            expected_targets=expected_targets,
            actual_targets=actual_targets,
        )
    if expected_dimensions and actual_dimensions != expected_dimensions:
        _raise_context_contract_violation(
            source,
            "llm_dimensions_not_preserved",
            "LLM 解析维度未完整进入 current_task",
            expected_dimensions=expected_dimensions,
            actual_dimensions=actual_dimensions,
        )
    logger.info(
        "execution_context_contract_bound",
        source=source,
        target_supplier_names=actual_targets,
        analysis_dimensions=actual_dimensions,
        reference_count=len(contract.references),
    )
    return execution_context


def build_agent_supervisor_graph_input(
    execution_context: dict[str, Any],
    *,
    run_id: str,
    user_message: str,
) -> dict[str, Any]:
    """Build the only permitted Supervisor graph input from shared context."""
    context = validate_execution_context(
        execution_context, source="agent_supervisor_graph_input"
    )
    intent: dict[str, Any] = {"current_task": context["current_task"]}
    llm_intent = context.get("llm_intent")
    if isinstance(llm_intent, dict) and "requested_action" in llm_intent:
        intent["requested_action"] = llm_intent.get("requested_action") or "none"
    return {
        "run_id": run_id,
        "user_query": user_message,
        "supplier_references": context["references"],
        "intent": intent,
        "conversation_state": context["conversation_state"],
    }


def _raise_context_contract_violation(
    source: str,
    reason: str,
    message: str,
    **details: Any,
) -> None:
    logger.error(
        "context_contract_violation",
        source=source,
        reason=reason,
        **details,
    )
    raise ExecutionContextContractViolation(message)


def _text_list(value: Any) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def collect_supplier_references(
    existing_references: list[dict[str, Any]],
    value: Any,
    source: str,
) -> list[dict[str, Any]]:
    """Merge supplier evidence from one graph event through the shared extractor."""
    from app.services.agent import _dedupe_references, extract_supplier_references

    return _dedupe_references([
        *existing_references,
        *_collect_normalized_references(value, source),
        *extract_supplier_references(value, source),
    ])


def _collect_normalized_references(value: Any, source: str) -> list[dict[str, Any]]:
    """Preserve already-normalized ``{name: ...}`` references.

    Tool payloads normally use ``supplier_name``/``company_name`` and are
    handled by the shared extractor. Harness persistence passes normalized
    references back through this function, so ignoring ``name`` silently
    erased PostgreSQL conversation references.
    """
    candidates = value if isinstance(value, list) else [value]
    result: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        if item.get("kind") != "supplier" and not any(
            item.get(field)
            for field in (
                "supplier_id", "company_id", "candidate_id", "result_id", "supplier_code",
                "monitor_target_id", "target_type",
            )
        ):
            continue
        result.append({**item, "name": name.strip(), "source": item.get("source") or source})
    return result


def load_execution_context(
    session_id: str,
    user_message: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Load control-plane facts first, using Mongo only for legacy bootstrap."""
    from app.graphs.agent_core.intent_extractor import extract_conversation_intent

    context: dict[str, Any] | None = None
    if user_id:
        from app.domains.agent_run.state_store import session_state_store

        context = session_state_store.get_execution_context(session_id, user_id)
    if context is None:
        from app.services.agent import _load_conversation_context

        legacy = _load_conversation_context(session_id)
        context = {
            "history": legacy.get("history", []),
            "references": legacy.get("references", []),
            "state": legacy.get("state", {}),
        }
    execution_context = build_execution_context(
        session_id=session_id,
        user_message=user_message,
        history=context.get("history", []),
        references=context.get("references", []),
        previous_state=context.get("conversation_state", context.get("state", {})),
    )
    extracted = extract_conversation_intent(user_message, execution_context["references"])
    resolved = apply_extracted_conversation_intent(execution_context, extracted)
    resolved = _enforce_scope_query_intent(resolved, user_message)
    return validate_execution_context(
        _apply_harness_sourcing_requirement(resolved, user_message),
        source="load_execution_context",
    )


def _enforce_scope_query_intent(
    execution_context: dict[str, Any], user_message: str
) -> dict[str, Any]:
    """Keep range-level procurement questions free of hallucinated suppliers.

    LLM extraction is useful for aliases and dimensions, but a question such as
    ``查看监控清单`` has no supplier target by design.  If a model happens to
    return a target for that wording, it would incorrectly switch the Harness
    from the scope tool to a single-supplier analysis.  The deterministic
    message contract is authoritative for these four range-level intents.
    """
    scope_tokens = (
        "监控清单", "监控列表", "我负责的供应商", "我管理的供应商",
        "我科室", "本部门", "待复核", "待审核", "待处理事项",
    )
    if not any(token in str(user_message or "") for token in scope_tokens):
        return execution_context
    # A message can mention “监控清单” while explicitly requesting a write,
    # for example “把上海海拉电子有限公司加入到监控清单中”. Preserve that
    # action and its target so Chat API can route it to the approval workflow.
    from app.graphs.agent_core.intent_extractor import has_explicit_watchlist_request

    if has_explicit_watchlist_request(user_message):
        # The explicit write verb is authoritative even when the LLM is
        # unavailable or conservatively returns requested_action="none".
        # Populate the same structured field consumed by Chat API routing so
        # the request always reaches the durable approval workflow.
        current_task = dict(execution_context.get("current_task") or {})
        llm_intent = execution_context.get("llm_intent")
        normalized_intent = dict(llm_intent) if isinstance(llm_intent, dict) else {}
        normalized_intent["requested_action"] = "add_watchlist"
        if not normalized_intent.get("target_supplier_names"):
            normalized_intent["target_supplier_names"] = list(
                current_task.get("target_supplier_names") or []
            )
        return {
            **execution_context,
            "llm_intent": normalized_intent,
        }
    current_task = dict(execution_context.get("current_task") or {})
    current_task.update({
        "target_supplier_names": [],
        "analysis_dimensions": [],
        "subtasks": [],
        "task_type": "analysis",
        "user_message": user_message,
    })
    conversation_state = dict(execution_context.get("conversation_state") or {})
    conversation_state.update({
        "selected_supplier_names": [],
        "selected_suppliers": [],
        "current_task": current_task,
    })
    llm_intent = execution_context.get("llm_intent")
    if isinstance(llm_intent, dict):
        llm_intent = {
            **llm_intent,
            "target_supplier_names": [],
            "analysis_dimensions": [],
            "task_type": "analysis",
            "requested_action": "none",
        }
    return {
        **execution_context,
        "conversation_state": conversation_state,
        "current_task": current_task,
        **({"llm_intent": llm_intent} if isinstance(llm_intent, dict) else {}),
    }


def _apply_harness_sourcing_requirement(
    execution_context: dict[str, Any],
    user_message: str,
) -> dict[str, Any]:
    """Bind one validated sourcing requirement before the Harness graph starts."""
    current_task = dict(execution_context.get("current_task") or {})
    if current_task.get("task_type") != "sourcing":
        return execution_context
    existing = current_task.get("requirement")
    if not isinstance(existing, dict):
        existing = (execution_context.get("conversation_state") or {}).get("current_requirement")
    from app.domains.sourcing_risk.requirement_service import resolve_harness_requirement

    resolved = resolve_harness_requirement(
        user_message,
        existing if isinstance(existing, dict) else None,
    )
    conversation_state = dict(execution_context.get("conversation_state") or {})
    current_task["requirement_status"] = resolved.get("status")
    current_task["requirement_extraction_source"] = resolved.get("extraction_source")
    if resolved.get("status") == "ready":
        requirement = dict(resolved["requirement"])
        current_task["requirement"] = requirement
        conversation_state["current_requirement"] = requirement
    else:
        current_task["requirement_missing"] = list(resolved.get("missing") or ["category"])
    conversation_state["current_task"] = current_task
    return {
        **execution_context,
        "conversation_state": conversation_state,
        "current_task": current_task,
    }


def apply_extracted_conversation_intent(
    execution_context: dict[str, Any],
    extracted: Any,
) -> dict[str, Any]:
    """Overlay validated LLM intent onto the one shared execution context."""
    if extracted is None:
        return execution_context

    target_names = list(getattr(extracted, "target_supplier_names", []) or [])
    dimensions = list(getattr(extracted, "analysis_dimensions", []) or [])
    task_type = getattr(extracted, "task_type", "none")
    requested_action = getattr(extracted, "requested_action", "none")
    if not target_names and not dimensions and task_type == "none" and requested_action == "none":
        return execution_context

    conversation_state = dict(execution_context.get("conversation_state") or {})
    current_task = dict(execution_context.get("current_task") or {})
    resolved = resolve_turn(
        str(current_task.get("user_message") or ""),
        session_id=str(execution_context.get("session_id") or ""),
        previous_memory=memory_from_state(conversation_state, session_id=str(execution_context.get("session_id") or "")),
        references=execution_context.get("references") or [],
        llm_candidates=target_names,
    )
    # LLM-extracted explicit names are the validated source of truth for this
    # turn. The deterministic resolver is still used for pronouns/aliases,
    # but must not prepend conversational verbs such as “将” to a company
    # name and then violate the shared context contract.
    target_names = target_names or resolved.target_supplier_names
    if target_names:
        current_task["target_supplier_names"] = target_names
        conversation_state["selected_supplier_names"] = target_names
        conversation_state["selected_suppliers"] = target_names
    if dimensions:
        current_task["analysis_dimensions"] = dimensions
    elif target_names:
        # A company-only follow-up means "run the last explicit analysis for
        # this company". Do not inherit sourcing or an old execution plan.
        dimensions = _text_list(current_task.get("analysis_dimensions"))
    if task_type == "sourcing" and not target_names:
        current_task["task_type"] = "sourcing"
    elif target_names or dimensions:
        current_task["task_type"] = "analysis"
    conversation_state["entity_memory"] = resolved.memory.model_dump(mode="json")
    conversation_state["focus_set"] = resolved.focus_set.model_dump(mode="json") if resolved.focus_set else None
    conversation_state["entity_resolution"] = resolved.model_dump(
        mode="json", exclude={"memory", "focus_set"}
    )
    if target_names and dimensions:
        planned = plan_supplier_analysis_task(
            task_id=str(current_task.get("task_id") or "current-task"),
            supplier_names=target_names,
            dimensions=dimensions,
        ).model_dump(mode="json")
        planned["user_message"] = str(current_task.get("user_message") or "")
        current_task = planned
    conversation_state["current_task"] = current_task
    return {
        **execution_context,
        "conversation_state": conversation_state,
        "current_task": current_task,
        "llm_intent": extracted.model_dump(mode="json"),
    }


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
    resolution = resolve_turn(
        user_message,
        session_id=session_id,
        turn_id=str(previous_state.get("current_turn_id") or "current-turn") if isinstance(previous_state, dict) else "current-turn",
        previous_memory=memory_from_state(previous_state, session_id=session_id),
        references=normalized_references,
    )
    conversation_state["entity_memory"] = resolution.memory.model_dump(mode="json")
    conversation_state["focus_set"] = resolution.focus_set.model_dump(mode="json") if resolution.focus_set else None
    conversation_state["entity_resolution"] = resolution.model_dump(
        mode="json", exclude={"memory", "focus_set"}
    )
    if resolution.target_supplier_names:
        conversation_state["selected_supplier_names"] = resolution.target_supplier_names
        conversation_state["selected_suppliers"] = resolution.target_supplier_names
    current_task = dict(conversation_state.get("current_task") or {})
    if resolution.target_supplier_names:
        current_task["target_supplier_names"] = resolution.target_supplier_names
        current_task["task_type"] = "analysis" if current_task.get("analysis_dimensions") else current_task.get("task_type", "sourcing")
    from app.graphs.agent_core.intent_extractor import infer_task_type

    inferred_task_type = infer_task_type(user_message)
    if inferred_task_type == "sourcing":
        current_task["task_type"] = "sourcing"
    targets = list(current_task.get("target_supplier_names") or [])
    dimensions = list(current_task.get("analysis_dimensions") or [])
    previous_task = previous_state.get("current_task") if isinstance(previous_state, dict) else None
    previous_dimensions = (
        [str(item).strip() for item in previous_task.get("analysis_dimensions", []) if str(item).strip()]
        if isinstance(previous_task, dict)
        else []
    )
    if targets and not dimensions and previous_dimensions:
        # A new explicit company name may be a terse follow-up to the last
        # analysis request. Inherit only dimensions, never old subtasks/plans.
        dimensions = list(dict.fromkeys(previous_dimensions))
        current_task["analysis_dimensions"] = dimensions
        current_task["task_type"] = "analysis"
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
    raw_conversation_state = execution_context.get("conversation_state")
    conversation_state = (
        raw_conversation_state if isinstance(raw_conversation_state, dict) else {}
    )
    raw_task = execution_context.get("current_task")
    task = raw_task if isinstance(raw_task, dict) else {}
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
