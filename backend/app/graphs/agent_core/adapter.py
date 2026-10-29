"""Shared ConversationState adapter for every chat execution graph."""

from __future__ import annotations

import json
import hashlib
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.logging import get_logger
from app.graphs.agent_core.capability_routing import is_generic_sourcing_category
from app.graphs.agent_core.entity_memory import memory_from_state, resolve_turn
from app.graphs.agent_core.planner import plan_supplier_analysis_task
from app.services.conversation_state import build_conversation_state

logger = get_logger()
_SOURCING_MATERIAL_NUMBER_PATTERN = re.compile(r"物料号(?:为|是|[:：])?\s*(\d{4,})")


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
    expected_capability = str(llm_intent.get("capability") or "none")
    expected_action = str(llm_intent.get("requested_action") or "none")
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
    if (
        expected_dimensions
        and actual_dimensions != expected_dimensions
        and expected_action not in {"remove_watchlist", "batch_add_watchlist", "manage_scheduled_report"}
    ):
        _raise_context_contract_violation(
            source,
            "llm_dimensions_not_preserved",
            "LLM 解析维度未完整进入 current_task",
            expected_dimensions=expected_dimensions,
            actual_dimensions=actual_dimensions,
        )
    if expected_capability != "none" and current_task.get("capability") != expected_capability:
        _raise_context_contract_violation(
            source,
            "llm_capability_not_preserved",
            "LLM 解析能力未完整进入 current_task",
            expected_capability=expected_capability,
            actual_capability=current_task.get("capability"),
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


def collect_sourcing_candidate_context(
    outcomes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Freeze the latest sourcing candidate order for the next conversation turn.

    Candidate ordinals are meaningful only within one discovery result.  This
    snapshot keeps the displayed order and stable candidate IDs together so a
    follow-up such as ``选第 2 家外部候选`` cannot accidentally select an older
    session reference or rerun discovery without consuming the user's choice.
    """
    sourcing_tools = {
        "discover_supplier_candidates", "search_suppliers", "find_alternatives",
        "list_formal_suppliers",
    }
    latest: list[dict[str, Any]] = []
    for outcome in outcomes:
        if not isinstance(outcome, dict) or str(outcome.get("tool_name") or "") not in sourcing_tools:
            continue
        data = outcome.get("data")
        if not isinstance(data, dict):
            continue
        rows: list[Any] = []
        for key in ("candidates", "local_candidates", "external_candidates", "results", "items"):
            value = data.get(key)
            if isinstance(value, list):
                rows.extend(value)
        latest = [row for row in rows if isinstance(row, dict)]
    if not latest:
        return None
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(latest, start=1):
        name = str(row.get("supplier_name") or row.get("company_name") or row.get("name") or "").strip()
        if not name:
            continue
        key = str(row.get("candidate_id") or row.get("supplier_id") or row.get("company_id") or name)
        if key in seen:
            continue
        seen.add(key)
        candidate = {
            "rank": len(candidates) + 1,
            "name": name,
            "candidate_id": row.get("candidate_id") or row.get("supplier_id") or row.get("company_id"),
            "candidate_type": row.get("candidate_type") or (
                "external" if row.get("status") == "staged_candidate" else "formal"
            ),
            "identity_status": row.get("identity_status") or row.get("status"),
            "source": row.get("source") or row.get("discovery_source"),
        }
        candidates.append({key: value for key, value in candidate.items() if value not in (None, "")})
    if not candidates:
        return None
    version = hashlib.sha256(
        json.dumps(candidates, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return {"version": version, "candidates": candidates}


_ORDINAL_FOLLOW_UP = re.compile(r"第\s*(?P<number>[0-9一二三四五六七八九十百]+)\s*[家个名]")
_CHINESE_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_candidate_ordinal(message: str) -> int | None:
    match = _ORDINAL_FOLLOW_UP.search(str(message or ""))
    if not match:
        return None
    value = match.group("number")
    if value.isdigit():
        ordinal = int(value)
    elif value == "十":
        ordinal = 10
    elif len(value) == 2 and value[0] == "十" and value[1] in _CHINESE_NUMBERS:
        ordinal = 10 + _CHINESE_NUMBERS[value[1]]
    elif len(value) == 2 and value[1] == "十" and value[0] in _CHINESE_NUMBERS:
        ordinal = _CHINESE_NUMBERS[value[0]] * 10
    else:
        ordinal = _CHINESE_NUMBERS.get(value)
    return ordinal if ordinal and ordinal > 0 else None


def _apply_sourcing_follow_up(
    execution_context: dict[str, Any],
    user_message: str,
) -> dict[str, Any]:
    """Consume ordinal/no-match sourcing follow-ups before Harness planning."""
    conversation_state = dict(execution_context.get("conversation_state") or {})
    snapshot = conversation_state.get("sourcing_candidates")
    candidates = snapshot.get("candidates") if isinstance(snapshot, dict) else None
    current_task = dict(execution_context.get("current_task") or {})
    message = str(user_message or "")
    no_match_tokens = ("没有合适", "没有满意", "无合适", "没找到合适", "候选怎么办", "都不合适")
    explicit_stop = "不要重复调用" in message and isinstance(candidates, list)
    if any(token in message for token in no_match_tokens) or explicit_stop:
        current_task.update({
            "task_type": "sourcing",
            "capability": "sourcing",
            "analysis_dimensions": ["sourcing"],
            "sourcing_follow_up": "no_match",
            "selected_candidate_id": None,
        })
        conversation_state["current_task"] = current_task
        return {**execution_context, "conversation_state": conversation_state, "current_task": current_task}

    ordinal = _parse_candidate_ordinal(message)
    if ordinal is None or not isinstance(candidates, list):
        return execution_context
    filtered = candidates
    if "外部" in message:
        filtered = [
            item for item in candidates
            if isinstance(item, dict) and str(item.get("candidate_type") or "").lower() in {"external", "external_candidate", "staged"}
        ]
    selected = filtered[ordinal - 1] if 0 < ordinal <= len(filtered) else None
    if not isinstance(selected, dict):
        current_task["sourcing_follow_up"] = "ordinal_out_of_range"
        current_task["candidate_ordinal"] = ordinal
        current_task["candidate_list_version"] = snapshot.get("version") if isinstance(snapshot, dict) else None
        conversation_state["current_task"] = current_task
        return {**execution_context, "conversation_state": conversation_state, "current_task": current_task}
    name = str(selected.get("name") or "").strip()
    selected_id = str(selected.get("candidate_id") or "").strip() or None
    current_task.update({
        "target_supplier_names": [name],
        "selected_candidate_id": selected_id,
        "selected_candidate_name": name,
        "candidate_ordinal": ordinal,
        "candidate_list_version": snapshot.get("version") if isinstance(snapshot, dict) else None,
        "sourcing_follow_up": "candidate_verification" if any(token in message for token in ("验证", "核验", "确认")) else "candidate_selected",
        "task_type": "sourcing",
        "capability": "sourcing",
        "analysis_dimensions": ["sourcing"],
    })
    if any(token in message for token in ("验证", "核验", "确认")):
        current_task["identity_verification"] = True
    conversation_state["selected_supplier_names"] = [name]
    conversation_state["selected_suppliers"] = [name]
    conversation_state["selected_candidate_id"] = selected_id
    conversation_state["current_task"] = current_task
    return {**execution_context, "conversation_state": conversation_state, "current_task": current_task}


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
    """Load only user-owned control-plane facts into the execution context."""
    from app.graphs.agent_core.intent_extractor import extract_conversation_intent

    context: dict[str, Any] | None = None
    if user_id:
        from app.domains.agent_run.state_store import session_state_store

        context = session_state_store.get_execution_context(session_id, user_id)
    if context is None:
        context = {
            "history": [],
            "references": [],
            "state": {},
        }
    execution_context = build_execution_context(
        session_id=session_id,
        user_message=user_message,
        history=context.get("history", []),
        references=context.get("references", []),
        previous_state=context.get("conversation_state", context.get("state", {})),
    )
    previous_state = context.get("conversation_state", context.get("state", {}))
    extracted = extract_conversation_intent(
        user_message,
        execution_context["references"],
        conversation_context={
            "session_id": session_id,
            "history": execution_context["history"],
            "conversation_state": previous_state if isinstance(previous_state, dict) else {},
        },
    )
    resolved = apply_extracted_conversation_intent(execution_context, extracted)
    resolved = _enforce_scope_query_intent(resolved, user_message)
    resolved = _apply_identity_verification_intent(resolved, user_message)
    resolved = _apply_monitor_target_id_intent(resolved, user_message)
    resolved = _apply_harness_sourcing_requirement(resolved, user_message)
    resolved = _apply_sourcing_follow_up(resolved, user_message)
    resolved = _persist_input_understanding(
        resolved,
        user_message=user_message,
        previous_state=previous_state if isinstance(previous_state, dict) else {},
    )
    return validate_execution_context(resolved, source="load_execution_context")


def _persist_input_understanding(
    execution_context: dict[str, Any],
    *,
    user_message: str,
    previous_state: dict[str, Any],
) -> dict[str, Any]:
    """Store the final normalized parse with deterministic per-field provenance."""
    from app.graphs.agent_core.intent_extractor import (
        ConversationIntentExtraction,
        _conversation_context_snapshot,
        _derive_field_sources,
        explicit_write_action,
        should_extract_conversation_intent,
    )

    current_task = execution_context.get("current_task")
    current_task = current_task if isinstance(current_task, dict) else {}
    if not should_extract_conversation_intent(user_message) and explicit_write_action(user_message) == "none":
        conversation_state = dict(execution_context.get("conversation_state") or {})
        conversation_state.pop("input_understanding", None)
        return {**execution_context, "conversation_state": conversation_state}
    raw_intent = execution_context.get("llm_intent")
    raw_intent = raw_intent if isinstance(raw_intent, dict) else {}
    target_names = raw_intent.get("target_supplier_names")
    if not isinstance(target_names, list) or not target_names:
        target_names = current_task.get("target_supplier_names", [])
    dimensions = raw_intent.get("analysis_dimensions")
    if not isinstance(dimensions, list):
        dimensions = current_task.get("analysis_dimensions", [])
    raw_task_type = raw_intent.get("task_type")
    if raw_task_type not in {"sourcing", "analysis", "comparison", "none"}:
        raw_task_type = current_task.get("task_type")
    if raw_task_type not in {"sourcing", "analysis", "comparison", "none"}:
        raw_task_type = "analysis" if target_names or dimensions else "none"
    requested_action = raw_intent.get("requested_action") or explicit_write_action(user_message)
    extraction = ConversationIntentExtraction(
        target_supplier_names=target_names,
        analysis_dimensions=dimensions,
        provider_capabilities=raw_intent.get("provider_capabilities") or current_task.get("provider_capabilities", []),
        capability=raw_intent.get("capability") or current_task.get("capability") or "none",
        scope=raw_intent.get("scope") or current_task.get("scope") or "none",
        sourcing_requirement=(
            raw_intent.get("sourcing_requirement")
            or current_task.get("requirement")
            or current_task.get("llm_sourcing_requirement")
        ),
        time_range=raw_intent.get("time_range"),
        task_type=raw_task_type,
        requested_action=requested_action,
        confidence=float(raw_intent.get("confidence") or 0.0),
        interpretation_summary=str(raw_intent.get("interpretation_summary") or ""),
        ambiguities=_text_list(raw_intent.get("ambiguities"))[:5],
        missing_fields=[
            field for field in _text_list(raw_intent.get("missing_fields"))[:4]
            if field in {
                "target_supplier_names", "analysis_dimensions",
                "sourcing_requirement", "time_range",
            }
        ],
        needs_clarification=bool(raw_intent.get("needs_clarification")),
        clarification_question=(
            str(raw_intent.get("clarification_question") or "")[:300] or None
        ),
    )
    context_snapshot = _conversation_context_snapshot({
        "history": execution_context.get("history", []),
        "conversation_state": previous_state,
    })
    field_sources = _derive_field_sources(
        user_message,
        extraction,
        execution_context.get("references", []),
        context_snapshot,
    )
    entity_resolution = (execution_context.get("conversation_state") or {}).get("entity_resolution") or {}
    if (
        extraction.target_supplier_names
        and (
            entity_resolution.get("reason") == "ordinal_reference"
            or re.search(r"第[一二三四五六七八九十\d]+家|前[两三四五六七八九十\d]+家|排名第一", user_message)
        )
    ):
        field_sources["target_supplier_names"] = "conversation_state"
    understanding = extraction.model_copy(update={"field_sources": field_sources}).model_dump(
        mode="json", exclude_none=True
    )
    conversation_state = dict(execution_context.get("conversation_state") or {})
    conversation_state["input_understanding"] = understanding
    if not extraction.needs_clarification:
        conversation_state.pop("pending_clarification", None)
    return {
        **execution_context,
        "conversation_state": conversation_state,
        "llm_intent": understanding,
    }


def _apply_identity_verification_intent(
    execution_context: dict[str, Any], user_message: str
) -> dict[str, Any]:
    """Bind explicit monitor identity checks to a deterministic Harness task.

    Identity verification is a read-only operation and must not be inferred
    from a generic risk dimension.  The monitor target UUID in the message is
    treated as the stable lookup key; the company name remains display context.
    """
    from app.graphs.agent_core.intent_extractor import (
        extract_monitor_target_id,
        is_identity_verification_request,
    )

    if not is_identity_verification_request(user_message):
        return execution_context
    current_task = dict(execution_context.get("current_task") or {})
    target_id = extract_monitor_target_id(user_message)
    target_names = [
        str(item).strip()
        for item in current_task.get("target_supplier_names", [])
        if str(item).strip()
    ]
    if not target_names:
        # Keep the common quoted form usable even when the legacy resolver did
        # not produce a conversation entity for this turn.
        import re

        match = re.search(r"[“\"「『]([^”\"」』]+)[”\"」』]", user_message)
        if match:
            target_names = [match.group(1).strip()]
    current_task.update({
        "target_supplier_names": target_names,
        "analysis_dimensions": ["identity_review"],
        "task_type": "analysis",
        "capability": "identity_review",
        "scope": "single_supplier",
        "identity_verification": True,
        "monitor_target_id": target_id,
        "subtasks": [],
        "user_message": user_message,
    })
    conversation_state = dict(execution_context.get("conversation_state") or {})
    conversation_state["selected_supplier_names"] = target_names
    conversation_state["selected_suppliers"] = target_names
    conversation_state["current_task"] = current_task
    llm_intent = execution_context.get("llm_intent")
    normalized_intent = dict(llm_intent) if isinstance(llm_intent, dict) else {}
    normalized_intent.update({
        "target_supplier_names": target_names,
        "analysis_dimensions": ["identity_review"],
        "task_type": "analysis",
        "capability": "identity_review",
        "scope": "single_supplier",
        "requested_action": "none",
        "monitor_target_id": target_id,
    })
    return {
        **execution_context,
        "conversation_state": conversation_state,
        "current_task": current_task,
        "llm_intent": normalized_intent,
    }


def _apply_monitor_target_id_intent(
    execution_context: dict[str, Any], user_message: str
) -> dict[str, Any]:
    """Resolve a stable monitor-target UUID to its display name for analysis.

    Users often paste the ID from the monitoring page and ask for a risk
    review without repeating the supplier name.  The UUID is the authoritative
    lookup key; loading its name here keeps the Harness plan executable while
    preserving the same permission-scoped target reference.
    """
    from app.graphs.agent_core.intent_extractor import extract_monitor_target_id

    target_id = extract_monitor_target_id(user_message)
    if not target_id:
        return execution_context
    current_task = dict(execution_context.get("current_task") or {})
    names = [str(item).strip() for item in current_task.get("target_supplier_names", []) if str(item).strip()]
    if names:
        current_task["monitor_target_id"] = target_id
        return {**execution_context, "current_task": current_task}
    try:
        from app.domains.alert.service import _find_watchlist_target

        target = _find_watchlist_target(monitor_target_id=target_id)
    except Exception as exc:
        logger.warning("monitor_target_id_resolution_failed", monitor_target_id=target_id, error=str(exc))
        target = None
    if not isinstance(target, dict):
        return execution_context
    name = str(target.get("company_name") or target.get("display_name") or "").strip()
    if not name:
        return execution_context
    current_task.update({
        "target_supplier_names": [name],
        "monitor_target_id": target_id,
        "task_type": "analysis",
        "user_message": user_message,
    })
    conversation_state = dict(execution_context.get("conversation_state") or {})
    conversation_state["selected_supplier_names"] = [name]
    conversation_state["selected_suppliers"] = [name]
    conversation_state["current_task"] = current_task
    references = list(execution_context.get("references") or [])
    if not any(isinstance(item, dict) and item.get("monitor_target_id") == target_id for item in references):
        references.append({
            "kind": "supplier",
            "name": name,
            "monitor_target_id": target_id,
            "target_type": target.get("target_type"),
            "supplier_id": target.get("supplier_id"),
            "company_id": target.get("company_id"),
            "identity_status": target.get("identity_status"),
            "source": "monitor_target_id",
        })
    return {
        **execution_context,
        "references": references,
        "conversation_state": conversation_state,
        "current_task": current_task,
    }


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
        "本人负责供应商", "本人负责的供应商", "本人管理供应商", "本人管理的供应商",
        "我所监控的供应商", "我监控的供应商", "当前监控供应商",
        "我科室", "本部门", "待复核", "待审核", "待处理事项",
    )
    if not any(token in str(user_message or "") for token in scope_tokens):
        return execution_context
    # A message can mention “监控清单” while explicitly requesting a write,
    # for example “把上海海拉电子有限公司加入到监控清单中”. Preserve that
    # action and its target so Chat API can route it to the approval workflow.
    from app.graphs.agent_core.intent_extractor import explicit_write_action

    write_action = explicit_write_action(user_message)
    if write_action != "none":
        # The explicit write verb is authoritative even when the LLM is
        # unavailable or conservatively returns requested_action="none".
        # Populate the same structured field consumed by Chat API routing so
        # the request always reaches the durable approval workflow.
        current_task = dict(execution_context.get("current_task") or {})
        llm_intent = execution_context.get("llm_intent")
        normalized_intent = dict(llm_intent) if isinstance(llm_intent, dict) else {}
        normalized_intent["requested_action"] = write_action
        if not normalized_intent.get("target_supplier_names"):
            normalized_intent["target_supplier_names"] = list(
                current_task.get("target_supplier_names") or []
            )
        return {
            **execution_context,
            "llm_intent": normalized_intent,
        }
    current_task = dict(execution_context.get("current_task") or {})
    existing_capability = str(
        (execution_context.get("llm_intent") or {}).get("capability") or ""
    )
    scope_capability = (
        existing_capability
        if existing_capability in {"risk_trend", "watchlist_scope"}
        else "watchlist_scope"
    )
    current_task.update({
        "target_supplier_names": [],
        "analysis_dimensions": [],
        "subtasks": [],
        "task_type": "analysis",
        "capability": scope_capability,
        "scope": "responsible_suppliers",
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
            "capability": scope_capability,
            "scope": "responsible_suppliers",
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
    conversation_state = dict(execution_context.get("conversation_state") or {})
    prior_conversation_state = dict(conversation_state)
    if current_task.get("task_type") != "sourcing" and not _is_sourcing_constraint_follow_up(
        current_task,
        conversation_state,
        user_message,
    ):
        return execution_context
    # LLM intent extraction is intentionally best-effort.  If a follow-up
    # contains only budget/cost/delivery wording, a transient empty model
    # response must not demote the existing sourcing session to an analysis
    # task with no executable subtasks.
    if current_task.get("task_type") != "sourcing":
        current_task.update({
            "task_type": "sourcing",
            "capability": "sourcing",
            "scope": "product_category",
            "analysis_dimensions": ["sourcing"],
            "subtasks": [],
        })
    existing = current_task.get("requirement")
    if not isinstance(existing, dict):
        existing = conversation_state.get("current_requirement")
    from app.domains.sourcing_risk.requirement_service import (
        resolve_harness_requirement,
        resolve_harness_requirement_from_llm,
    )

    llm_requirement = current_task.pop("llm_sourcing_requirement", None)
    turn_explicit_slots = _sourcing_constraint_slots(user_message)
    if isinstance(llm_requirement, dict):
        resolved = resolve_harness_requirement_from_llm(llm_requirement)
        # A sourcing follow-up may contain only a new constraint (for example,
        # “预算有限/成本更优”).  Keep the validated category/specification from
        # the previous turn and merge only explicitly supplied slots; otherwise
        # the second turn regresses into a category clarification loop.
        resolved_requirement = resolved.get("requirement") if isinstance(resolved, dict) else None
        same_category = (
            isinstance(existing, dict)
            and isinstance(resolved_requirement, dict)
            and str(resolved_requirement.get("category") or resolved_requirement.get("product") or "").strip()
            == str(existing.get("category") or existing.get("product") or "").strip()
        )
        if (
            isinstance(existing, dict)
            and existing.get("category")
            and existing.get("specification")
            and (
                resolved.get("status") != "ready"
                or _is_generic_sourcing_constraint(
                    user_message,
                    resolved.get("requirement") if isinstance(resolved, dict) else None,
                )
                or (same_category and bool(_sourcing_constraint_slots(user_message)))
            )
        ):
            merged_requirement = dict(existing)
            merge_source = (
                resolved.get("requirement")
                if resolved.get("status") == "ready"
                and isinstance(resolved.get("requirement"), dict)
                else llm_requirement
            )
            for key, value in merge_source.items():
                if value is None or value == "" or value == []:
                    continue
                if (
                    key == "supply_region"
                    and not turn_explicit_slots.get("supply_region")
                    and turn_explicit_slots.get("region")
                    and str(value).strip() == str(turn_explicit_slots["region"]).strip()
                    and existing.get("supply_region")
                ):
                    continue
                if key in {"category", "product", "material", "specification"} and _is_generic_sourcing_constraint(
                    user_message,
                    merge_source,
                ):
                    continue
                if key == "optional_conditions":
                    prior = list(merged_requirement.get(key) or [])
                    merged_requirement[key] = list(dict.fromkeys(prior + list(value)))
                else:
                    merged_requirement[key] = value
            for key, value in _sourcing_constraint_slots(user_message).items():
                if key == "optional_conditions":
                    prior = list(merged_requirement.get(key) or [])
                    merged_requirement[key] = list(dict.fromkeys(prior + list(value)))
                else:
                    merged_requirement[key] = value
            merged = resolve_harness_requirement_from_llm(merged_requirement)
            if merged.get("status") == "ready":
                merged["extraction_source"] = "context_merge"
                resolved = merged
    else:
        resolved = resolve_harness_requirement(
            user_message,
            existing if isinstance(existing, dict) else None,
        )
        resolved_requirement = resolved.get("requirement") if isinstance(resolved, dict) else None
        same_category = (
            isinstance(existing, dict)
            and isinstance(resolved_requirement, dict)
            and str(resolved_requirement.get("category") or resolved_requirement.get("product") or "").strip()
            == str(existing.get("category") or existing.get("product") or "").strip()
        )
        if (
            isinstance(existing, dict)
            and existing.get("category")
            and existing.get("specification")
            and resolved.get("status") == "ready"
            and _is_generic_sourcing_constraint(
                user_message,
                resolved.get("requirement") if isinstance(resolved, dict) else None,
            )
            or (
                isinstance(existing, dict)
                and existing.get("category")
                and existing.get("specification")
                and resolved.get("status") == "ready"
                and same_category
                and bool(_sourcing_constraint_slots(user_message))
            )
        ):
            generic_requirement = resolved.get("requirement")
            merged_requirement = dict(existing)
            if isinstance(generic_requirement, dict):
                for key, value in generic_requirement.items():
                    if value is None or value == "" or value == []:
                        continue
                    if key in {"category", "product", "material", "specification"}:
                        continue
                    if key == "optional_conditions":
                        prior = list(merged_requirement.get(key) or [])
                        merged_requirement[key] = list(dict.fromkeys(prior + list(value)))
                    else:
                        merged_requirement[key] = value
            for key, value in _sourcing_constraint_slots(user_message).items():
                if key == "optional_conditions":
                    prior = list(merged_requirement.get(key) or [])
                    merged_requirement[key] = list(dict.fromkeys(prior + list(value)))
                else:
                    merged_requirement[key] = value
            resolved = {
                "status": "ready",
                "requirement": merged_requirement,
                "extraction_source": "context_merge",
            }
    existing_category = (
        str(existing.get("category") or existing.get("product") or "").strip()
        if isinstance(existing, dict)
        else ""
    )
    has_specific_existing_category = bool(existing_category) and not _is_generic_sourcing_constraint(
        "", {"category": existing_category}
    )
    resolved_requirement = resolved.get("requirement") if isinstance(resolved, dict) else None
    if (
        resolved.get("status") == "ready"
        and _is_generic_sourcing_constraint(user_message, resolved_requirement)
        and not has_specific_existing_category
    ):
        # Broad suitability wording is not a procurement category. Preserve the
        # sourcing intent, but require the user to identify what to source.
        resolved = {
            "status": "clarification_required",
            "requirement": {},
            "missing": ["category"],
            "extraction_source": resolved.get("extraction_source"),
        }
    current_task["requirement_status"] = resolved.get("status")
    current_task["requirement_extraction_source"] = resolved.get("extraction_source")
    if resolved.get("status") == "ready":
        requirement = dict(resolved["requirement"])
        explicit_slots = _sourcing_constraint_slots(user_message)
        preferred_region = explicit_slots.get("region")
        explicit_delivery_region = explicit_slots.get("supply_region")
        prior_delivery_region = existing.get("supply_region") if isinstance(existing, dict) else None
        prior_supplier_region = existing.get("region") if isinstance(existing, dict) else None
        if (
            explicit_delivery_region
            and not preferred_region
            and not prior_supplier_region
            and str(requirement.get("region") or "").strip()
            == str(explicit_delivery_region).strip()
        ):
            # A destination may be copied into both slots by the model. Keep
            # it as a delivery constraint, not a supplier-location filter.
            requirement.pop("region", None)
        if (
            preferred_region
            and not explicit_delivery_region
            and not prior_delivery_region
            and str(requirement.get("supply_region") or "").strip() == str(preferred_region).strip()
        ):
            # Models sometimes map “优先华东” to the delivery destination.
            # Keep the supplier preference in `region`; only a delivery phrase
            # or a previously captured destination can populate `supply_region`.
            requirement.pop("supply_region", None)
        for key, value in explicit_slots.items():
            requirement[key] = value
        price_preference = ("优先考虑价格", "优先价格", "成本更优", "成本更低", "更便宜", "低成本", "低价")
        if any(token in user_message for token in price_preference):
            requirement["must_have"] = [
                item for item in requirement.get("must_have", [])
                if not any(term in str(item) for term in ("价格", "成本", "低价", "便宜"))
            ]
        if explicit_slots:
            prior_extraction_source = resolved.get("extraction_source")
            resolved = resolve_harness_requirement_from_llm(requirement)
            if prior_extraction_source == "context_merge" and resolved.get("status") == "ready":
                resolved["extraction_source"] = "context_merge"
        if resolved.get("status") == "ready":
            requirement = dict(resolved["requirement"])
            current_task["requirement"] = requirement
            conversation_state["current_requirement"] = requirement
        else:
            current_task["requirement_missing"] = list(resolved.get("missing") or ["category"])
        current_task["requirement_status"] = resolved.get("status")
        current_task["requirement_extraction_source"] = resolved.get("extraction_source")
    else:
        current_task["requirement_missing"] = list(resolved.get("missing") or ["category"])
        current_task["requirement_status"] = resolved.get("status")
        current_task["requirement_extraction_source"] = resolved.get("extraction_source")
    llm_intent = execution_context.get("llm_intent")
    if resolved.get("status") == "ready" and isinstance(llm_intent, dict):
        from app.graphs.agent_core.intent_extractor import _sourcing_specification_is_required

        requirement = resolved.get("requirement") or current_task.get("requirement") or {}
        specification_context = dict(prior_conversation_state)
        if isinstance(existing, dict) and not specification_context.get("current_requirement"):
            specification_context["current_requirement"] = existing
        is_material_number_lookup = (
            current_task.get("task_type") == "sourcing"
            and len(_SOURCING_MATERIAL_NUMBER_PATTERN.findall(user_message)) == 1
        )
        requires_specification = not is_material_number_lookup and _sourcing_specification_is_required(
            user_message, requirement, specification_context,
        )
        missing_now = list(llm_intent.get("missing_fields") or [])
        if is_material_number_lookup:
            llm_intent = {
                **llm_intent,
                "needs_clarification": False,
                "missing_fields": [],
                "clarification_question": None,
            }
        elif requires_specification:
            if "sourcing_requirement" not in missing_now:
                missing_now.append("sourcing_requirement")
            llm_intent = {
                **llm_intent,
                "needs_clarification": True,
                "missing_fields": missing_now,
                "clarification_question": "请补充产品的具体规格或技术要求，我再继续筛选供应商。",
            }
        elif "sourcing_requirement" in missing_now:
            missing_fields = [
                field for field in missing_now
                if field != "sourcing_requirement"
            ]
            llm_intent = {
                **llm_intent,
                "needs_clarification": bool(missing_fields),
                "missing_fields": missing_fields,
                "clarification_question": None if not missing_fields else llm_intent.get("clarification_question"),
            }
    conversation_state["current_task"] = current_task
    if isinstance(llm_intent, dict):
        conversation_state["input_understanding"] = llm_intent
    return {
        **execution_context,
        "conversation_state": conversation_state,
        "current_task": current_task,
        **({"llm_intent": llm_intent} if isinstance(llm_intent, dict) else {}),
    }


def _is_sourcing_constraint_follow_up(
    current_task: dict[str, Any],
    conversation_state: dict[str, Any],
    user_message: str,
) -> bool:
    """Recognize a constraint-only follow-up when intent extraction is empty.

    A completed sourcing turn leaves a validated requirement or candidate
    snapshot in the durable context.  In that context, a message with no
    explicit company target and procurement constraints is still a sourcing
    turn, even if the optional LLM extraction times out or returns an empty
    object.
    """
    if current_task.get("target_supplier_names"):
        return False
    has_context = bool(
        isinstance(current_task.get("requirement"), dict)
        or isinstance(conversation_state.get("current_requirement"), dict)
        or isinstance(conversation_state.get("sourcing_candidates"), dict)
    )
    if not has_context:
        return False
    message = str(user_message or "")
    return any(token in message for token in (
        "预算", "成本", "交付周期", "交付", "候选", "筛选", "替代供应商", "认证", "供货",
    ))


def _is_generic_sourcing_constraint(
    user_message: str,
    requirement: dict[str, Any] | None,
) -> bool:
    """Recognize vague cost/constraint wording that must not replace a category.

    LLM extraction can turn a follow-up such as “预算有限，找成本更优的替代
    供应商” into a synthetic category (for example ``成本更优的替代``).  That
    wording is a constraint on the previous product, not a new procurement
    category.  Keep the rule narrow so an explicit category such as ``安全带``
    still replaces the previous sourcing requirement.
    """
    payload = requirement if isinstance(requirement, dict) else {}
    category = str(payload.get("category") or payload.get("product") or "").strip()
    message = str(user_message or "")
    generic_category_tokens = (
        "成本更优",
        "成本更低",
        "更便宜",
        "低成本",
        "低价",
        "替代供应商",
        "替代方案",
        "预算有限",
    )
    if category and (
        is_generic_sourcing_category(category)
        or any(token in category for token in generic_category_tokens)
    ):
        return True
    if any(token in message for token in generic_category_tokens) and not category:
        return True
    return False


def _sourcing_constraint_slots(user_message: str) -> dict[str, Any]:
    """Extract a small deterministic set of safe follow-up constraints."""
    message = str(user_message or "")
    slots: dict[str, Any] = {}
    preferred_region = re.search(
        r"(?:优先(?:考虑|选择)?|倾向(?:于)?)\s*(华东|华南|华北|西南|西北|东北)(?:地区)?",
        message,
    )
    if preferred_region:
        slots["region"] = preferred_region.group(1)
    else:
        region_before_supplier = re.search(
            r"(华东|华南|华北|西南|西北|东北)(?:地区)?[^，,。；;\n]{0,16}(?:供应商|厂家|厂商)",
            message,
        )
        destination = re.search(
            r"(?:交付|交货|配送)(?:到|至)\s*([\u4e00-\u9fffA-Za-z0-9（）()]{2,24})",
            message,
        )
        if region_before_supplier and not (
            destination and destination.group(1).startswith(region_before_supplier.group(1))
        ):
            slots["region"] = region_before_supplier.group(1)
    budget_clear = re.search(r"预算\s*(?:先|暂时|暂)?\s*(?:不考虑|不设(?:硬性)?限制|不限|取消)", message)
    if budget_clear:
        slots["budget"] = None
    elif "预算有限" in message:
        slots["budget"] = "预算有限"
    budget_match = re.search(
        r"(?P<tax>含税)?\s*预算\s*(?P<bound>不超过|最多|上限|控制在)?\s*(?P<amount>\d+(?:\.\d+)?)(?P<spacing>\s*)(?P<unit>万元?|万|千元?|元)",
        message,
    )
    if budget_match and not budget_clear:
        amount, spacing, unit = budget_match.group("amount", "spacing", "unit")
        normalized_unit = {"万": "万元", "千": "千元"}.get(unit, unit)
        bound = budget_match.group("bound")
        tax = "含税" if budget_match.group("tax") or "含税" in message[:budget_match.start()] else ""
        if bound:
            slots["budget"] = f"{tax}{bound}{amount}{spacing}{normalized_unit}"
        else:
            slots["budget"] = f"{amount}{spacing}{normalized_unit}"
    destination = re.search(r"(?:交付|交货|配送)(?:到|至)\s*([\u4e00-\u9fffA-Za-z0-9（）()]{2,24})", message)
    if destination:
        slots["supply_region"] = destination.group(1).rstrip("，,。；;")
    delivery_time = re.search(
        r"((?:\d+(?:\.\d+)?|[一二三四五六七八九十]+)(?:个)?(?:工作日|天|周|个月|月|年)内)(?:交付|交货)",
        message,
    )
    if delivery_time:
        slots["delivery"] = delivery_time.group(1)
    if re.search(r"优先\s*交(?:付|货)周期|交(?:付|货)周期优先", message):
        slots["delivery"] = "优先交货周期" if "交货周期" in message else "优先交付周期"
    optional: list[str] = []
    for token in ("优先考虑价格", "优先价格", "成本更优", "成本更低", "更便宜", "低成本", "低价"):
        if token in message:
            optional.append(token)
    if "总成本" in message:
        optional.append("总成本优先")
    if "交付周期" in message:
        slots["delivery"] = "交付周期优先"
        optional.append("交付周期优先")
    if optional:
        slots["optional_conditions"] = list(dict.fromkeys(optional))
    return slots


def apply_extracted_conversation_intent(
    execution_context: dict[str, Any],
    extracted: Any,
) -> dict[str, Any]:
    """Overlay validated LLM intent onto the one shared execution context."""
    from app.graphs.agent_core.capability_routing import (
        has_durable_control_expression,
        is_explicit_no_task,
        is_non_operational_request,
    )

    initial_task = dict(execution_context.get("current_task") or {})
    user_message = str(initial_task.get("user_message") or "")
    requested_action = getattr(extracted, "requested_action", "none")
    if (
        requested_action == "none"
        and not has_durable_control_expression(user_message)
        and (is_non_operational_request(user_message) or is_explicit_no_task(extracted))
    ):
        # build_execution_context also performs deterministic pre-parsing. A
        # meta/general turn must clear that provisional task and any pending
        # clarification before the Harness plans the current turn.
        conversation_state = dict(execution_context.get("conversation_state") or {})
        cleared_task = {
            "task_type": "none",
            "capability": "none",
            "scope": "none",
            "target_supplier_names": [],
            "analysis_dimensions": [],
            "subtasks": [],
            "user_message": user_message,
        }
        if initial_task.get("task_id"):
            cleared_task["task_id"] = initial_task["task_id"]
        conversation_state["current_task"] = cleared_task
        conversation_state.pop("pending_clarification", None)
        conversation_state.pop("current_requirement", None)
        intent_snapshot = (
            extracted.model_dump(mode="json")
            if extracted is not None and callable(getattr(extracted, "model_dump", None))
            else {
                "target_supplier_names": [],
                "analysis_dimensions": [],
                "capability": "none",
                "scope": "none",
                "task_type": "none",
                "requested_action": "none",
            }
        )
        conversation_state["input_understanding"] = intent_snapshot
        return {
            **execution_context,
            "conversation_state": conversation_state,
            "current_task": cleared_task,
            "llm_intent": intent_snapshot,
        }

    if extracted is None:
        from app.graphs.agent_core.intent_extractor import explicit_write_action

        current_task = dict(execution_context.get("current_task") or {})
        conversation_state = dict(execution_context.get("conversation_state") or {})
        pending = conversation_state.get("pending_clarification")
        if isinstance(pending, dict) and pending:
            targets = _text_list(
                current_task.get("target_supplier_names")
                or pending.get("target_supplier_names")
            )
            dimensions = _text_list(
                current_task.get("analysis_dimensions")
                or pending.get("analysis_dimensions")
            )
            provider_capabilities = _text_list(
                current_task.get("provider_capabilities")
                or pending.get("provider_capabilities")
            )
            capability = str(current_task.get("capability") or pending.get("capability") or "none")
            task_type = str(pending.get("task_type") or current_task.get("task_type") or "analysis")
            if task_type == "none" and (targets or dimensions or provider_capabilities):
                task_type = "analysis"
            current_task.update({
                "target_supplier_names": targets,
                "analysis_dimensions": dimensions,
                "provider_capabilities": provider_capabilities,
                "capability": capability,
                "task_type": task_type,
            })
            for key in ("scope", "time_range", "requirement"):
                if pending.get(key) is not None:
                    current_task[key] = pending[key]
            if task_type == "comparison":
                current_task["comparison"] = True
            missing_fields = [
                field for field in pending.get("missing_fields", [])
                if not (
                    field == "target_supplier_names" and targets
                    or field == "analysis_dimensions" and dimensions
                )
            ]
            conversation_state["selected_supplier_names"] = targets
            conversation_state["selected_suppliers"] = targets
            conversation_state["current_task"] = current_task
            if missing_fields:
                pending = {**pending, "missing_fields": missing_fields}
                conversation_state["pending_clarification"] = pending
            else:
                conversation_state.pop("pending_clarification", None)
            restored_intent = {
                **pending,
                "target_supplier_names": targets,
                "analysis_dimensions": dimensions,
                "provider_capabilities": provider_capabilities,
                "capability": capability,
                "task_type": task_type,
                "scope": current_task.get("scope", "none"),
                "needs_clarification": bool(missing_fields),
                "missing_fields": missing_fields,
                "requested_action": "none",
                "confidence": 0.35,
            }
            return {
                **execution_context,
                "conversation_state": conversation_state,
                "current_task": current_task,
                "llm_intent": restored_intent,
            }
        action = explicit_write_action(str(current_task.get("user_message") or ""))
        if action != "none":
            current_task.update({
                "task_type": "action_draft",
                "analysis_dimensions": [],
                "subtasks": [],
                "user_message": str(current_task.get("user_message") or ""),
            })
            conversation_state = dict(execution_context.get("conversation_state") or {})
            conversation_state["current_task"] = current_task
            conversation_state["input_understanding"] = {
                "target_supplier_names": list(current_task.get("target_supplier_names") or []),
                "analysis_dimensions": [],
                "capability": "none",
                "scope": "single_supplier",
                "task_type": "none",
                "requested_action": action,
                "confidence": 0.0,
                "field_sources": {},
            }
            return {
                **execution_context,
                "conversation_state": conversation_state,
                "current_task": current_task,
                "llm_intent": {
                    "target_supplier_names": list(current_task.get("target_supplier_names") or []),
                    "analysis_dimensions": [],
                    "capability": "none",
                    "scope": "single_supplier",
                    "task_type": "analysis",
                    "requested_action": action,
                },
            }
        return execution_context

    target_names = list(getattr(extracted, "target_supplier_names", []) or [])
    dimensions = list(getattr(extracted, "analysis_dimensions", []) or [])
    provider_capabilities = list(getattr(extracted, "provider_capabilities", []) or [])
    capability = getattr(extracted, "capability", "none")
    scope = getattr(extracted, "scope", "none")
    sourcing_requirement = getattr(extracted, "sourcing_requirement", None)
    task_type = getattr(extracted, "task_type", "none")
    requested_action = getattr(extracted, "requested_action", "none")
    time_range = getattr(extracted, "time_range", None)
    is_watchlist_write = requested_action in {
        "add_watchlist", "remove_watchlist", "batch_add_watchlist",
    }
    is_write_action = requested_action != "none"
    if (
        not target_names
        and not dimensions
        and not provider_capabilities
        and capability == "none"
        and scope == "none"
        and sourcing_requirement is None
        and task_type == "none"
        and requested_action == "none"
    ):
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
    # Ambiguous aliases and singular references must remain targetless even
    # when the model or an earlier context overlay supplied candidate names.
    # Explicit current-turn identities resolve without this ambiguity status.
    if resolved.needs_clarification and resolved.reason in {
        "ambiguous_alias", "ambiguous_singular_reference",
    }:
        target_names = []
        current_task["target_supplier_names"] = []
        conversation_state["selected_supplier_names"] = []
        conversation_state["selected_suppliers"] = []
    else:
        # LLM-extracted explicit names are validated for this turn. The
        # deterministic resolver also handles pronouns/aliases, but must not
        # prepend conversational verbs such as “将” to a company name.
        target_names = target_names or resolved.target_supplier_names
    if target_names:
        current_task["target_supplier_names"] = target_names
        conversation_state["selected_supplier_names"] = target_names
        conversation_state["selected_suppliers"] = target_names
    if requested_action in {"remove_watchlist", "batch_add_watchlist", "manage_scheduled_report"}:
        # Structured side-effect requests own the turn even when the model
        # also emits analysis dimensions such as ``risk``. The write workflow
        # must not inherit a read-only execution plan.
        dimensions = []
        current_task["analysis_dimensions"] = []
        current_task["subtasks"] = []
        current_task["task_type"] = "action_draft"
    elif requested_action == "add_watchlist" and not dimensions:
        # A watchlist follow-up with no current-turn analysis request must not
        # inherit dimensions from the previous identity or risk analysis.
        dimensions = []
        current_task["analysis_dimensions"] = []
        current_task["subtasks"] = []
        current_task["task_type"] = "action_draft"
    elif dimensions:
        current_task["analysis_dimensions"] = dimensions
    elif (
        not is_write_action
        and capability not in {"none", "risk", "sourcing"}
        and target_names
    ):
        # A specialized capability such as supply-chain contagion, identity
        # review or report generation may intentionally have no dimension in
        # the supplier risk matrix. Do not retain a generic risk subtask that
        # was assembled before LLM intent overlay; Harness will append the
        # capability-specific task from the normalized capability instead.
        dimensions = []
        current_task["analysis_dimensions"] = []
        current_task["subtasks"] = []
    elif target_names:
        # A company-only follow-up means "run the last explicit analysis for
        # this company". Do not inherit sourcing or an old execution plan.
        dimensions = _text_list(current_task.get("analysis_dimensions"))
    if provider_capabilities:
        current_task["provider_capabilities"] = provider_capabilities
    if capability != "none":
        current_task["capability"] = capability
        current_task["scope"] = scope
    if capability == "sourcing" and sourcing_requirement is not None:
        current_task["llm_sourcing_requirement"] = sourcing_requirement.model_dump(
            mode="json", exclude_none=True
        )
    if time_range:
        current_task["time_range"] = time_range
    if capability == "sourcing" or task_type == "sourcing":
        current_task["task_type"] = "sourcing"
    elif task_type == "comparison":
        current_task["task_type"] = "comparison"
        current_task["comparison"] = True
    elif (target_names or dimensions) and not is_write_action:
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
        if time_range:
            planned["time_range"] = time_range
        if provider_capabilities:
            planned["provider_capabilities"] = provider_capabilities
        # The planner returns the executable task matrix, but capability and
        # scope are routing facts from the same LLM extraction.  Preserve
        # them when replacing the pre-plan task so the contract validator can
        # prove that the active graph will execute the requested capability.
        if capability != "none":
            planned["capability"] = capability
        if task_type == "comparison":
            planned["task_type"] = "comparison"
            planned["comparison"] = True
        if scope != "none":
            planned["scope"] = scope
        if is_write_action:
            planned["task_type"] = "action_draft"
        current_task = planned
    elif is_write_action:
        current_task["task_type"] = "action_draft"
    conversation_state["current_task"] = current_task
    intent_snapshot = extracted.model_dump(mode="json")
    conversation_state["input_understanding"] = intent_snapshot
    logger.info(
        "conversation_intent_execution_overlay",
        session_id=execution_context.get("session_id"),
        normalized_intent={
            "target_supplier_names": intent_snapshot.get("target_supplier_names", []),
            "analysis_dimensions": intent_snapshot.get("analysis_dimensions", []),
            "capability": intent_snapshot.get("capability", "none"),
        },
        executable_task={
            "target_supplier_names": current_task.get("target_supplier_names", []),
            "analysis_dimensions": current_task.get("analysis_dimensions", []),
            "capability": current_task.get("capability", "none"),
            "task_type": current_task.get("task_type", "none"),
        },
    )
    return {
        **execution_context,
        "conversation_state": conversation_state,
        "current_task": current_task,
        "llm_intent": intent_snapshot,
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
    elif inferred_task_type == "none" and not current_task.get("target_supplier_names") and not current_task.get("analysis_dimensions"):
        current_task["task_type"] = "none"
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
    conversation_state["current_task"] = current_task

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
    sourcing_candidates: dict[str, Any] | None = None,
    current_requirement: dict[str, Any] | None = None,
) -> None:
    """Persist every graph completion through the shared state-aware save path."""
    from app.services.agent import _save_turn

    if sourcing_candidates or current_requirement:
        _save_turn(
            session_id,
            user_message,
            answer,
            references or [],
            sourcing_candidates,
            current_requirement,
        )
    else:
        _save_turn(session_id, user_message, answer, references or [])
