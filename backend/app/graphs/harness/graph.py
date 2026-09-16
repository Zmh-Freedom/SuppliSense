"""The single, bounded LangGraph runtime for Agent work.

The graph deliberately accepts a resolved execution context and an explicit
task matrix. It does not read MongoDB, re-route intents, or call an LLM inside
nodes. Those responsibilities belong to the API/adapter and the future
planner integration; this makes the runtime deterministic and testable.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graphs.agent_core.answer_contract import AgentAnswer, build_agent_answer
from app.graphs.agent_core.evidence_ledger import (
    Claim,
    EvidenceLedger,
    EvidenceRecord,
    EvidenceStatus,
    build_evidence_record,
)
from app.tools import TOOL_REGISTRY
from app.tools.executor import ToolContext, ToolExecutor, ToolOutcome
from app.core.logging import get_logger
from app.graphs.harness.state import (
    ExecutionBudget,
    HarnessState,
    HarnessTask,
    new_budget,
    utc_now_iso,
)
from app.domains.risk.risk_contract import get_risk_dimension_spec


PersistCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]
ProgressCallback = Callable[[str, dict[str, Any], bool], Awaitable[None] | None]
NarrateCallback = Callable[[AgentAnswer, str], Awaitable[AgentAnswer] | AgentAnswer]

_DIMENSION_TO_TOOL = {
    dimension: (spec.tool_name, spec.argument_name)
    for dimension in (
        "risk", "financial", "business_risk", "quality", "delivery",
        "esg", "sentiment", "compliance",
    )
    if (spec := get_risk_dimension_spec(dimension)) is not None
}

logger = get_logger()


def _entity_id(name: str, context: Mapping[str, Any]) -> str:
    for reference in context.get("references", []):
        if not isinstance(reference, dict) or reference.get("name") != name:
            continue
        return str(
            reference.get("monitor_target_id")
            or reference.get("supplier_id")
            or reference.get("company_id")
            or reference.get("candidate_id")
            or f"entity:{name}"
        )
    return f"entity:{name}"


def _monitor_target_id(name: str, context: Mapping[str, Any]) -> str | None:
    for reference in context.get("references", []):
        if not isinstance(reference, dict) or reference.get("name") != name:
            continue
        value = reference.get("monitor_target_id")
        return str(value) if value else None
    return None


def _input_hash(arguments: dict[str, Any]) -> str:
    serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _task_from_subtask(
    task: Mapping[str, Any],
    subtask: Mapping[str, Any],
    context: Mapping[str, Any],
) -> HarnessTask | None:
    dimension = str(subtask.get("dimension") or "").strip()
    supplier_name = str(subtask.get("supplier_name") or "").strip()
    if dimension == "sourcing":
        requirement = task.get("requirement") or {}
        request_id = str(requirement.get("request_id") or "").strip() if isinstance(requirement, dict) else ""
        if request_id:
            return HarnessTask(
                task_id=str(subtask.get("subtask_id") or "sourcing"),
                tool_name="search_suppliers",
                arguments={"request_id": request_id},
                entity_id="sourcing",
                dimension="sourcing",
                resource_key="sourcing",
                required=bool(subtask.get("required", True)),
                evidence_requirements=["supplier_candidate"],
            )
        if isinstance(requirement, dict) and requirement.get("category"):
            return HarnessTask(
                task_id=str(subtask.get("subtask_id") or "sourcing"),
                tool_name="discover_supplier_candidates",
                arguments={"requirement": requirement},
                entity_id="sourcing",
                dimension="sourcing",
                resource_key="sourcing",
                required=bool(subtask.get("required", True)),
                evidence_requirements=["supplier_candidate"],
            )
        if not task.get("user_message") or _is_formal_directory_query(task):
            return HarnessTask(
                task_id=str(subtask.get("subtask_id") or "sourcing"),
                tool_name="list_formal_suppliers",
                arguments={"limit": 20},
                entity_id="sourcing",
                dimension="sourcing",
                resource_key="sourcing",
                required=bool(subtask.get("required", True)),
                evidence_requirements=["supplier_candidate"],
            )
        return None
    mapping = _DIMENSION_TO_TOOL.get(dimension)
    if not mapping or not supplier_name:
        return None
    tool_name, argument_name = mapping
    arguments = {argument_name: supplier_name}
    if tool_name == "assess_operational_risk":
        arguments["dimension"] = dimension
    return HarnessTask(
        task_id=str(subtask.get("subtask_id") or f"{task.get('task_id', 'task')}-{dimension}-{supplier_name}"),
        tool_name=tool_name,
        arguments=arguments,
        entity_id=_entity_id(supplier_name, context),
        dimension=dimension,
        depends_on=[str(item) for item in subtask.get("depends_on", []) if str(item).strip()],
        resource_key=_entity_id(supplier_name, context),
        required=bool(subtask.get("required", True)),
        evidence_requirements=[str(item) for item in subtask.get("evidence_requirements", [dimension])],
    )


def _build_default_plan(state: HarnessState) -> list[HarnessTask]:
    current_task = state.get("current_task") or {}
    context = state.get("execution_context") or {}
    explicit = state.get("task_specs") or []
    if explicit:
        return [HarnessTask.model_validate(item) for item in explicit]

    subtasks = current_task.get("subtasks", [])
    planned = [
        result
        for item in subtasks
        if isinstance(item, dict)
        for result in [_task_from_subtask(current_task, item, context)]
        if result is not None
    ]
    if planned:
        return _append_derived_tasks(planned, current_task, context)

    names = [str(item).strip() for item in current_task.get("target_supplier_names", []) if str(item).strip()]
    dimensions = [str(item).strip() for item in current_task.get("analysis_dimensions", []) if str(item).strip()]
    result: list[HarnessTask] = []
    user_message = str(current_task.get("user_message") or "")

    # Range-level procurement questions are first-class tasks.  They must not
    # fall through to the supplier matrix below, otherwise the runtime ends
    # with an empty plan and the misleading "no evidence" answer.
    scope_task = _scope_query_task(current_task, context)
    if scope_task is not None:
        return [scope_task]

    # A monitor identity check is a distinct read-only task.  It must run even
    # when no risk dimensions were requested; otherwise the user sees an empty
    # evidence panel for a perfectly actionable主体核验 request.
    if current_task.get("identity_verification"):
        target_id = str(current_task.get("monitor_target_id") or "").strip() or None
        name = names[0] if names else ""
        if target_id or name:
            arguments: dict[str, Any] = {}
            if target_id:
                arguments["monitor_target_id"] = target_id
            if name:
                arguments["company_name"] = name
            identity_task = HarnessTask(
                task_id=f"{current_task.get('task_id', 'task')}:identity_review",
                tool_name="resolve_monitor_identity",
                arguments=arguments,
                entity_id=target_id or _entity_id(name, context),
                dimension="identity_review",
                resource_key=target_id or _entity_id(name, context),
                required=True,
                evidence_requirements=["identity_review"],
            )
            if name:
                return [identity_task, HarnessTask(
                    task_id=f"{current_task.get('task_id', 'task')}:tianyancha_identity",
                    tool_name="lookup_company_identity",
                    arguments={"company_name": name},
                    entity_id=target_id or _entity_id(name, context),
                    dimension="identity_review",
                    depends_on=[identity_task.task_id],
                    resource_key=target_id or _entity_id(name, context),
                    required=True,
                    evidence_requirements=["identity_review"],
                )]
            return [identity_task]

    if current_task.get("task_type") == "sourcing":
        requirement = current_task.get("requirement") or {}
        request_id = str(requirement.get("request_id") or "").strip() if isinstance(requirement, dict) else ""
        if isinstance(requirement, dict) and requirement.get("category") and not request_id:
            result.append(
                HarnessTask(
                    task_id=f"{current_task.get('task_id', 'task')}:sourcing",
                    tool_name="discover_supplier_candidates",
                    arguments={"requirement": requirement},
                    entity_id="sourcing",
                    dimension="sourcing",
                    resource_key="sourcing",
                    required=True,
                    evidence_requirements=["supplier_candidate"],
                )
            )
        elif _is_formal_directory_query(current_task):
            result.append(
                HarnessTask(
                    task_id=f"{current_task.get('task_id', 'task')}:sourcing",
                    tool_name="search_suppliers" if request_id else "list_formal_suppliers",
                    arguments={"request_id": request_id} if request_id else {"limit": 20},
                    entity_id="sourcing",
                    dimension="sourcing",
                    resource_key="sourcing",
                    required=True,
                    evidence_requirements=["supplier_candidate"],
                )
            )
    for name in dict.fromkeys(names):
        if "监控" in user_message and any(token in user_message for token in ("调查", "补全", "资料", "加入", "添加")):
            result.append(
                HarnessTask(
                    task_id=f"{current_task.get('task_id', 'task')}:{name}:monitoring_intake",
                    tool_name="investigate_supplier_monitoring",
                    arguments={"query": name},
                    entity_id=_entity_id(name, context),
                    dimension="risk_monitoring",
                    resource_key=_entity_id(name, context),
                    required=True,
                    evidence_requirements=["risk_monitoring"],
                )
            )
        for dimension in dict.fromkeys(dimensions):
            mapping = _DIMENSION_TO_TOOL.get(dimension)
            if not mapping:
                continue
            tool_name, argument_name = mapping
            arguments = {argument_name: name}
            if tool_name == "assess_operational_risk":
                arguments["dimension"] = dimension
            result.append(
                HarnessTask(
                    task_id=f"{current_task.get('task_id', 'task')}:{name}:{dimension}",
                    tool_name=tool_name,
                    arguments=arguments,
                    entity_id=_entity_id(name, context),
                    dimension=dimension,
                    resource_key=_entity_id(name, context),
                    required=dimension != "sentiment",
                    evidence_requirements=[dimension],
                )
            )
    return _append_derived_tasks(result, current_task, context)


def _scope_query_task(
    current_task: Mapping[str, Any], context: Mapping[str, Any]
) -> HarnessTask | None:
    """Map monitoring/ownership questions to explicit read-only tools."""
    message = str(current_task.get("user_message") or "").strip()
    if not message:
        return None
    from app.graphs.agent_core.intent_extractor import has_explicit_watchlist_request

    # “加入/纳入监控清单” is a write request. It must continue through the
    # durable approval path instead of being mistaken for a read-only list
    # query merely because the phrase contains “监控清单”.
    if has_explicit_watchlist_request(message):
        return None
    task_id = str(current_task.get("task_id") or "task")
    if any(token in message for token in ("待复核", "待审核", "待处理事项")):
        return HarnessTask(
            task_id=f"{task_id}:review_queue",
            tool_name="get_monitor_review_queue",
            arguments={},
            entity_id="review_queue",
            dimension="risk_monitoring",
            resource_key="review_queue",
            required=True,
            evidence_requirements=["risk_monitoring"],
        )
    trend_requested = any(token in message for token in ("风险变化", "风险趋势", "趋势", "变化情况"))
    monitoring_scope = any(token in message for token in ("监控清单", "监控列表", "我负责的供应商", "我管理的供应商", "我科室", "本部门"))
    if not monitoring_scope:
        return None
    return HarnessTask(
        task_id=f"{task_id}:watchlist:{'trend' if trend_requested else 'list'}",
        tool_name="analyze_watchlist_trend" if trend_requested else "get_watchlist",
        arguments={"period_months": 1} if trend_requested else {},
        entity_id="watchlist",
        dimension="risk_monitoring",
        resource_key="watchlist",
        required=True,
        evidence_requirements=["risk_monitoring"],
    )


def _is_formal_directory_query(task: Mapping[str, Any]) -> bool:
    """Only a directory question may use the unfiltered formal-supplier tool."""
    message = str(task.get("user_message") or "")
    return "正式供应商" in message and any(
        token in message for token in ("查询", "哪些", "列表", "目录", "清单", "有多少")
    )


def _append_derived_tasks(
    tasks: list[HarnessTask],
    current_task: Mapping[str, Any],
    context: Mapping[str, Any],
) -> list[HarnessTask]:
    """Add explicit trend/comparison nodes without changing the supplier matrix."""
    names = [
        str(item).strip()
        for item in current_task.get("target_supplier_names", [])
        if str(item).strip()
    ]
    message = str(current_task.get("user_message") or "")
    wants_trend = bool(current_task.get("include_trend")) or any(
        token in message for token in ("趋势", "历史变化", "变化情况")
    )
    wants_comparison = bool(current_task.get("comparison")) or any(
        token in message for token in ("对比", "比较", "横向")
    )
    existing_tool_names = {task.tool_name for task in tasks}
    task_prefix = str(current_task.get("task_id") or "task")
    if wants_trend and names and "analyze_trend" not in existing_tool_names:
        for name in dict.fromkeys(names):
            entity_id = _entity_id(name, context)
            dependencies = [
                task.task_id
                for task in tasks
                if task.entity_id == entity_id
                and task.dimension not in {"sourcing", "risk_trend"}
            ]
            tasks.append(
                HarnessTask(
                    task_id=f"{task_prefix}:{name}:trend",
                    tool_name="analyze_trend",
                    arguments={
                        "company_name": name,
                        "period_months": int(current_task.get("period_months") or 6),
                    },
                    entity_id=entity_id,
                    dimension="risk_trend",
                    depends_on=dependencies,
                    resource_key=entity_id,
                    evidence_requirements=["risk_trend"],
                )
            )
    if len(names) >= 2 and wants_comparison and "compare_companies" not in existing_tool_names:
        tasks.append(
            HarnessTask(
                task_id=f"{task_prefix}:comparison",
                tool_name="compare_companies",
                arguments={"company_names": list(dict.fromkeys(names))},
                entity_id="comparison",
                dimension="risk_comparison",
                depends_on=[task.task_id for task in tasks],
                resource_key="comparison",
                evidence_requirements=["risk_comparison"],
            )
        )
    _append_capability_tasks(tasks, current_task, context, names)
    _append_tianyancha_tasks(tasks, current_task, context, names)
    return tasks


def _append_capability_tasks(
    tasks: list[HarnessTask],
    current_task: Mapping[str, Any],
    context: Mapping[str, Any],
    names: list[str],
) -> None:
    """Bind explicit capability requests to the corresponding Harness tool."""
    if not names:
        return
    message = str(current_task.get("user_message") or "")
    requests = [
        (("舆情", "新闻", "负面信息"), "sentiment_analysis", "sentiment"),
        (("供应链关系", "关联关系", "传染风险", "风险传染"), "contagion_analysis", "risk_network"),
        (("预测", "未来", "趋势预测"), "predict_risk", "risk_prediction"),
        (("替代供应商", "备选供应商", "供应商替代"), "find_alternatives", "sourcing"),
        (("生成报告", "风险评估报告", "导出报告"), "generate_report", "report"),
    ]
    existing = {task.tool_name for task in tasks}
    prefix = str(current_task.get("task_id") or "task")
    for tokens, tool_name, dimension in requests:
        if tool_name in existing or not any(token in message for token in tokens):
            continue
        for name in dict.fromkeys(names):
            arguments: dict[str, Any] = {"company_name": name}
            if tool_name == "generate_report":
                arguments["report_type"] = "html"
            entity_id = _entity_id(name, context)
            tasks.append(HarnessTask(
                task_id=f"{prefix}:{name}:{dimension}",
                tool_name=tool_name,
                arguments=arguments,
                entity_id=entity_id,
                dimension=dimension,
                resource_key=entity_id,
                required=True,
                evidence_requirements=[dimension],
            ))
        existing.add(tool_name)


def _append_tianyancha_tasks(
    tasks: list[HarnessTask],
    current_task: Mapping[str, Any],
    context: Mapping[str, Any],
    names: list[str],
) -> None:
    """Map explicit provider/data requests to safe Tianyancha business tools."""
    if not names:
        return
    message = str(current_task.get("user_message") or "")
    requested = {
        str(item).strip()
        for item in current_task.get("provider_capabilities", [])
        if str(item).strip()
    }
    token_rules = {
        "identity": ("主体身份", "主体核验", "统一社会信用代码", "法人", "登记状态"),
        "legal_risk": ("司法", "诉讼", "被执行", "失信", "限制消费"),
        "business_risk": ("经营风险", "行政处罚", "经营异常", "严重违法", "股权质押", "欠税"),
        "news": ("舆情", "新闻", "负面信息"),
        "profile": ("工商资料", "注册资本", "注册地址", "历史变更", "股东", "分支机构"),
    }
    if "天眼查" in message:
        requested.add("identity")
    for capability, tokens in token_rules.items():
        if any(token in message for token in tokens):
            requested.add(capability)
    tool_by_capability = {
        "identity": ("lookup_company_identity", "identity_review"),
        "legal_risk": ("lookup_legal_risk", "legal_risk"),
        "business_risk": ("lookup_business_risk", "business_risk"),
        "news": ("lookup_company_news", "sentiment"),
        "profile": ("lookup_company_profile", "company_profile"),
    }
    existing = {task.tool_name for task in tasks}
    prefix = str(current_task.get("task_id") or "task")
    for capability in ("identity", "legal_risk", "business_risk", "news", "profile"):
        if capability not in requested:
            continue
        tool_name, dimension = tool_by_capability[capability]
        if tool_name in existing:
            continue
        for name in dict.fromkeys(names):
            entity_id = _entity_id(name, context)
            tasks.append(HarnessTask(
                task_id=f"{prefix}:{name}:{capability}:external",
                tool_name=tool_name,
                arguments={"company_name": name},
                entity_id=entity_id,
                dimension=dimension,
                resource_key=f"{entity_id}:tianyancha:{capability}",
                required=True,
                evidence_requirements=[dimension],
            ))
        existing.add(tool_name)


_ALLOWED_REMEDIATION_LOOP_TYPES = {"sourcing", "evidence", "provider_retry"}


def _remediation_specs(state: HarnessState) -> list[dict[str, Any]]:
    """Keep remediation loops explicit, typed and bounded before execution."""
    normalized: list[dict[str, Any]] = []
    for item in state.get("remediation_specs", []):
        if not isinstance(item, dict):
            continue
        loop_type = str(item.get("loop_type") or "evidence")
        if loop_type not in _ALLOWED_REMEDIATION_LOOP_TYPES:
            continue
        if not str(item.get("task_id") or "").strip() or not str(item.get("tool_name") or "").strip():
            continue
        normalized.append({**item, "loop_type": loop_type})
    return normalized


def _build_remediation_specs(
    state: HarnessState, tasks: list[HarnessTask]
) -> list[dict[str, Any]]:
    """Create one bounded fallback query for each missing evidence dimension.

    This policy is deliberately deterministic: it only chooses providers that
    already have a registered read-only contract and never asks the model to
    invent a new data source during a remediation loop.
    """
    existing_tools = {task.tool_name for task in tasks}
    task_prefix = str((state.get("current_task") or {}).get("task_id") or "task")
    names_by_dimension: dict[str, list[str]] = {}
    for task in tasks:
        name = str(task.arguments.get("company_name") or "").strip()
        if name:
            names_by_dimension.setdefault(task.dimension, []).append(name)

    fallback_tools: dict[str, tuple[str, str]] = {
        "risk": ("query_financials", "financial"),
        "financial": ("query_financials", "financial"),
        "risk_trend": ("analyze_trend", "risk_trend"),
        "sentiment": ("sentiment_analysis", "sentiment"),
        "compliance": ("check_sanctions", "compliance"),
        "esg": ("esg_assessment", "esg"),
    }
    specs: list[dict[str, Any]] = []
    missing_dimensions = (state.get("evidence_coverage") or {}).get("missing_dimensions")
    dimensions = missing_dimensions or list(dict.fromkeys(
        task.dimension for task in tasks if task.required
    ))
    for dimension in dimensions:
        mapping = fallback_tools.get(str(dimension))
        if mapping is None or mapping[0] in existing_tools:
            continue
        tool_name, loop_dimension = mapping
        names = list(dict.fromkeys(names_by_dimension.get(str(dimension), [])))
        for name in names:
            entity_id = _entity_id(name, state.get("execution_context") or {})
            arguments: dict[str, Any] = {"company_name": name}
            if tool_name == "analyze_trend":
                arguments["period_months"] = int((state.get("current_task") or {}).get("period_months") or 6)
            specs.append({
                "task_id": f"{task_prefix}:{name}:{loop_dimension}:fallback",
                "tool_name": tool_name,
                "arguments": arguments,
                "entity_id": entity_id,
                "dimension": loop_dimension,
                "resource_key": f"{entity_id}:{tool_name}",
                "required": True,
                "evidence_requirements": [loop_dimension],
                "loop_type": "provider_retry",
                "source_key": f"fallback:{tool_name}",
            })
    return specs


def _task_is_ready(task: HarnessTask, task_by_id: dict[str, HarnessTask]) -> bool:
    return all(
        dependency in task_by_id
        and task_by_id[dependency].status in {"completed", "partial"}
        for dependency in task.depends_on
    )


def _select_task_wave(
    tasks: list[HarnessTask],
    *,
    max_parallel_tasks: int,
    max_tasks: int,
) -> list[HarnessTask]:
    """Select a dependency-ready wave with one task per serialized resource."""
    task_by_id = {task.task_id: task for task in tasks}
    selected: list[HarnessTask] = []
    resources: set[str] = set()
    for task in tasks:
        if task.status != "pending" or not _task_is_ready(task, task_by_id):
            continue
        resource = str(task.resource_key or task.entity_id or task.task_id)
        if resource in resources:
            continue
        selected.append(task)
        resources.add(resource)
        if len(selected) >= min(max_parallel_tasks, max_tasks):
            break
    return selected


def _payload_evidence(
    outcome: ToolOutcome,
    task: HarnessTask,
) -> list[EvidenceRecord]:
    raw_records = outcome.data.get("evidence_records") or outcome.data.get("evidence") or []
    if not isinstance(raw_records, list):
        return []
    records: list[EvidenceRecord] = []
    for index, raw in enumerate(raw_records):
        if isinstance(raw, EvidenceRecord):
            records.append(raw)
            continue
        if not isinstance(raw, dict):
            continue
        try:
            records.append(EvidenceRecord.model_validate(raw))
            continue
        except Exception:
            pass
        payload = dict(raw)
        status_value = payload.pop("status", EvidenceStatus.AVAILABLE)
        try:
            status = EvidenceStatus(str(status_value))
        except ValueError:
            status = EvidenceStatus.UNAVAILABLE
        data_mode = str(payload.pop("data_mode", "formal"))
        if data_mode not in {"formal", "synthetic"}:
            data_mode = "formal"
        endpoint = payload.pop("endpoint", None)
        records.append(
            build_evidence_record(
                evidence_id=str(payload.pop("evidence_id", f"{outcome.call_id}:evidence:{index}")),
                entity_id=str(payload.pop("entity_id", task.entity_id)),
                dimension=str(payload.pop("dimension", task.dimension)),
                provider=str(payload.pop("provider", outcome.tool_name)),
                source_type=str(payload.pop("source_type", "unknown")),
                payload=payload,
                status=status,
                data_mode=data_mode,
                endpoint=endpoint,
            )
        )
    return records


def _payload_claims(outcome: ToolOutcome, task: HarnessTask) -> list[Claim]:
    raw_claims = outcome.data.get("claims") or []
    if not isinstance(raw_claims, list):
        return []
    claims: list[Claim] = []
    for index, raw in enumerate(raw_claims):
        if not isinstance(raw, dict):
            continue
        try:
            claims.append(
                Claim(
                    claim_id=str(raw.get("claim_id") or f"{outcome.call_id}:claim:{index}"),
                    entity_id=str(raw.get("entity_id") or task.entity_id),
                    dimension=str(raw.get("dimension") or task.dimension),
                    statement=str(raw["statement"]),
                    value=raw.get("value"),
                    fact_path=str(raw["fact_path"]) if raw.get("fact_path") else None,
                    operator=str(raw.get("operator") or "eq"),
                    unit=str(raw["unit"]) if raw.get("unit") else None,
                    evidence_refs=[str(item) for item in raw.get("evidence_refs", [])],
                    confidence=float(raw.get("confidence", 0.0)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return claims


def _required_dimensions(tasks: list[HarnessTask]) -> list[str]:
    return list(dict.fromkeys(task.dimension for task in tasks if task.required))


def _required_evidence_items(tasks: list[HarnessTask]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for task in tasks:
        if not task.required:
            continue
        requirements = task.evidence_requirements or [task.dimension]
        for requirement in requirements:
            items.append({
                "entity_id": task.entity_id,
                "dimension": task.dimension,
                "fact_path": (
                    requirement
                    if requirement not in {task.dimension, "supplier_candidate"}
                    else ""
                ),
            })
    return items


_SUMMARY_ROUTE_DEFINITIONS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("risk_network", ("供应链关系", "关联关系", "传染风险", "风险传染"), ("contagion_analysis",)),
    ("risk_prediction", ("预测", "未来", "趋势预测"), ("predict_risk",)),
    ("sentiment", ("舆情", "新闻", "负面信息"), ("sentiment_analysis", "lookup_company_news")),
    ("financial", ("财务", "财务数据"), ("query_financials",)),
    ("compliance", ("合规", "制裁", "黑名单"), ("check_sanctions",)),
    ("esg", ("ESG", "esg", "环境社会治理"), ("esg_assessment",)),
    ("risk_trend", ("历史变化", "风险趋势", "风险变化", "变化情况", "趋势"), ("analyze_trend",)),
    ("legal_risk", ("司法", "诉讼", "被执行", "失信", "限制消费"), ("lookup_legal_risk",)),
    ("business_risk", ("经营风险", "行政处罚", "经营异常", "严重违法", "股权质押", "欠税"), ("lookup_business_risk", "assess_business_risk")),
    ("report", ("报告", "导出报告"), ("generate_report",)),
    ("sourcing", ("找", "寻找", "寻源", "供应商候选", "替代供应商", "备选供应商", "供应商替代"), ("find_alternatives", "discover_supplier_candidates", "search_suppliers", "list_formal_suppliers")),
    ("company_profile", ("工商资料", "注册资本", "注册地址", "历史变更", "股东", "分支机构"), ("lookup_company_profile",)),
    ("identity_review", ("主体身份", "主体核验", "统一社会信用代码", "登记状态"), ("lookup_company_identity",)),
    ("risk_comparison", ("对比", "比较", "横向"), ("compare_companies",)),
    ("quality", ("质量", "质量风险"), ("assess_operational_risk",)),
    ("delivery", ("交付", "交付风险"), ("assess_operational_risk",)),
)


def _primary_summary_capability(message: str, tool_names: set[str], dimensions: set[str]) -> str | None:
    """Choose the user's explicitly requested capability for the lead summary."""
    matched: list[tuple[int, int, str]] = []
    for priority, (capability, tokens, tools) in enumerate(_SUMMARY_ROUTE_DEFINITIONS):
        if not tool_names.intersection(tools):
            continue
        positions = [message.find(token) for token in tokens if message.find(token) >= 0]
        if positions:
            matched.append((min(positions), priority, capability))
    if matched:
        return min(matched)[2]

    # These tools are only appended for explicit capability requests. Do not
    # use query_financials/assess_business_risk here: generic supplier review
    # intentionally includes those dimensions as supporting evidence.
    explicit_only_tools = {
        "contagion_analysis", "predict_risk", "analyze_trend", "compare_companies",
        "find_alternatives", "discover_supplier_candidates", "search_suppliers",
        "list_formal_suppliers", "generate_report", "lookup_company_profile",
        "lookup_company_identity", "lookup_legal_risk", "lookup_company_news",
    }
    for capability, _tokens, tools in _SUMMARY_ROUTE_DEFINITIONS:
        if tool_names.intersection(tools).intersection(explicit_only_tools):
            return capability
    # Do not infer a specialized lead from dimensions alone. Generic supplier
    # review intentionally carries financial/business dimensions as supporting
    # evidence, even though the user did not ask for a financial report.
    return None


def _latest_tool_data(outcomes: list[Any], tool_names: set[str]) -> dict[str, Any]:
    for outcome in reversed(outcomes):
        if not isinstance(outcome, dict):
            continue
        tool_name = str(outcome.get("tool_name") or outcome.get("tool") or "")
        data = outcome.get("data")
        if tool_name in tool_names and isinstance(data, dict):
            return data
    return {}


def _claim_values(claims: list[Any]) -> dict[str, Any]:
    return {
        str(claim.fact_path): claim.value
        for claim in claims
        if claim.fact_path and claim.value is not None
    }


def _summary_subject(target_names: list[str]) -> str:
    return "、".join(dict.fromkeys(target_names[:3])) or "该供应商"


def _summary_with_boundary(answer: AgentAnswer, result: str, normal_suffix: str) -> str:
    if answer.status in {"partial", "needs_review"} or answer.limitations:
        return result + "以上结论仅基于当前已取得资料，部分维度尚未覆盖；采购动作请先按下方提示核实。"
    return result + normal_suffix


def _summary(answer: AgentAnswer, state: HarnessState) -> str:
    current_task = state.get("current_task") or {}
    target_names = [
        str(item).strip()
        for item in current_task.get("target_supplier_names", [])
        if str(item).strip()
    ] if isinstance(current_task, dict) else []
    dimensions = {
        str(item).strip()
        for item in current_task.get("analysis_dimensions", [])
        if str(item).strip()
    } if isinstance(current_task, dict) else set()
    tasks = state.get("task_specs") or []
    tool_names = {
        str(item.get("tool_name") or item.get("tool") or "")
        for item in tasks
        if isinstance(item, dict)
    }
    outcomes = state.get("tool_outcomes") or []
    primary_capability = _primary_summary_capability(
        str(current_task.get("user_message") or ""),
        tool_names,
        dimensions,
    )
    latest_data: dict[str, Any] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        tool_name = str(outcome.get("tool_name") or outcome.get("tool") or "")
        if tool_name in {"get_watchlist", "analyze_watchlist_trend"}:
            data = outcome.get("data")
            if isinstance(data, dict):
                latest_data = data
    if "resolve_monitor_identity" in tool_names:
        identity_data = next(
            (
                outcome.get("data")
                for outcome in reversed(outcomes)
                if isinstance(outcome, dict)
                and outcome.get("tool_name") == "resolve_monitor_identity"
                and isinstance(outcome.get("data"), dict)
            ),
            {},
        )
        if isinstance(identity_data, dict):
            query = str(identity_data.get("query") or (target_names[0] if target_names else "该监控对象"))
            exact = identity_data.get("exact")
            candidates = identity_data.get("candidates") if isinstance(identity_data.get("candidates"), list) else []
            if exact:
                legal_name = str(exact.get("legal_name") or exact.get("name") or query)
                return f"已完成“{query}”的主体核验检索，找到可绑定的企业主体：{legal_name}。请在监控对象详情中确认绑定。"
            if candidates:
                return f"已完成“{query}”的主体核验检索，找到 {len(candidates)} 个候选主体，请在监控对象详情中选择并确认。"
            if identity_data.get("resolution") == "not_found":
                return f"未找到对应的监控对象，暂时无法为“{query}”执行主体核验。"
            return f"已完成“{query}”的主体核验检索，但当前没有可确认的主体候选。"
    if "get_watchlist" in tool_names:
        count = int(latest_data.get("count") or len(latest_data.get("companies") or []))
        scope = str(latest_data.get("scope") or "当前责任范围")
        if count == 0:
            return f"{scope}目前没有已纳入监控的供应商，暂时没有可展示的监控清单。"
        return f"已整理{scope}内的监控清单，共 {count} 家供应商，下面列出可直接查看的监控对象。"
    if "analyze_watchlist_trend" in tool_names:
        companies = latest_data.get("companies") if isinstance(latest_data.get("companies"), list) else []
        period = int(latest_data.get("period_months") or 1)
        if not companies:
            return f"当前责任范围内暂无可分析的监控供应商，暂时无法判断最近 {period} 个月的风险变化。"
        available = [item for item in companies if isinstance(item, dict) and item.get("trend") not in {None, "暂无数据", "数据不足"}]
        if not available:
            return f"已检查当前责任范围内 {len(companies)} 家供应商，但最近 {period} 个月的风险快照不足，暂时无法判断上升或下降。"
        return f"已完成当前责任范围内 {len(companies)} 家供应商最近 {period} 个月的风险变化检查，下面直接列出每家的变化状态和下一步建议。"
    subject = _summary_subject(target_names)
    if primary_capability == "financial":
        financial_claims = [claim for claim in answer.claims if claim.dimension == "financial"]
        values = _claim_values(financial_claims)
        if not financial_claims:
            return f"已完成 {subject} 的财务数据查询，但当前没有可验证的财务指标结果。"
        labels = {
            "revenue_growth": "营业收入同比",
            "net_profit_growth": "净利润同比",
            "debt_ratio": "资产负债率",
            "cash_flow": "经营现金流",
            "roe": "净资产收益率",
            "net_profit_margin": "净利率",
        }
        parts = [f"已完成 {subject} 的财务分析"]
        for path, label in labels.items():
            value = values.get(path)
            if not isinstance(value, (int, float)):
                continue
            if path in {"revenue_growth", "net_profit_growth", "debt_ratio", "roe", "net_profit_margin"}:
                parts.append(f"{label} {value * 100:.1f}%")
            else:
                parts.append(f"{label} {value:g}")
        return _summary_with_boundary(answer, "；".join(parts) + "。", "可结合财务明细和报告期趋势继续核查。")
    if primary_capability == "sentiment":
        sentiment_claims = [claim for claim in answer.claims if claim.dimension in {"sentiment", "news"}]
        values = _claim_values(sentiment_claims)
        if not sentiment_claims:
            return f"已完成 {subject} 的舆情检索，但当前没有可验证的新闻或负面信息结果。"
        sentiment_labels = {"negative": "负面", "neutral": "中性", "positive": "正面"}
        parts = [f"已完成 {subject} 的舆情分析"]
        if values.get("overall_sentiment") is not None:
            parts.append(f"总体倾向：{sentiment_labels.get(str(values['overall_sentiment']), str(values['overall_sentiment']))}")
        if isinstance(values.get("articles_count"), (int, float)):
            parts.append(f"纳入新闻 {int(values['articles_count'])} 条")
        if isinstance(values.get("negative_count"), (int, float)) and values["negative_count"] > 0:
            parts.append(f"负面新闻 {int(values['negative_count'])} 条")
        return _summary_with_boundary(answer, "；".join(parts) + "。", "可在下方逐条查看新闻摘要和来源链接。")
    if primary_capability == "compliance":
        compliance_claims = [claim for claim in answer.claims if claim.dimension == "compliance"]
        values = _claim_values(compliance_claims)
        if not compliance_claims:
            return f"已完成 {subject} 的合规筛查，但当前没有可验证的筛查结果。"
        clean = values.get("clean")
        match_count = values.get("match_count")
        result = f"已完成 {subject} 的合规与制裁筛查；" + (
            "当前未发现名单命中"
            if clean is True
            else f"发现名单命中 {int(match_count)} 条" if isinstance(match_count, (int, float)) else "发现需要人工核验的名单信号"
        ) + "。"
        return _summary_with_boundary(answer, result, "可结合命中记录和主体信息继续人工核验。")
    if primary_capability == "esg":
        esg_claims = [claim for claim in answer.claims if claim.dimension == "esg"]
        values = _claim_values(esg_claims)
        if not esg_claims:
            return f"已完成 {subject} 的 ESG 评估，但当前没有可验证的 ESG 结果。"
        parts = [f"已完成 {subject} 的 ESG 评估"]
        if values.get("total_score") is not None:
            parts.append(f"综合评分 {values['total_score']}/100")
        if values.get("total_level") is not None:
            parts.append(f"等级：{values['total_level']}")
        return _summary_with_boundary(answer, "；".join(parts) + "。", "可结合环境、社会和治理分项继续核查。")
    if primary_capability == "risk_trend":
        trend_claims = [claim for claim in answer.claims if claim.dimension == "risk_trend"]
        values = _claim_values(trend_claims)
        data = _latest_tool_data(outcomes, {"analyze_trend"})
        trend = values.get("trend", data.get("trend"))
        period = values.get("period_months", data.get("period_months"))
        if trend is None:
            return f"已完成 {subject} 的历史风险趋势查询，但当前风险快照不足，暂时无法判断变化方向。"
        trend_label = {"恶化": "风险恶化", "改善": "风险改善", "稳定": "风险基本稳定"}.get(str(trend), str(trend))
        period_text = f"最近 {int(period)} 个月" if isinstance(period, (int, float)) else "当前观察周期"
        return _summary_with_boundary(answer, f"已完成 {subject} {period_text}的历史风险趋势分析；判断为：{trend_label}。", "可结合历史风险快照查看具体变化节点。")
    if primary_capability in {"legal_risk", "business_risk"}:
        risk_claims = [claim for claim in answer.claims if claim.dimension == primary_capability]
        if not risk_claims:
            label = "司法风险" if primary_capability == "legal_risk" else "经营风险"
            return f"已完成 {subject} 的{label}检索，但当前没有可验证的明细结果。"
        label = "司法风险" if primary_capability == "legal_risk" else "经营风险"
        positive = sum(1 for claim in risk_claims if isinstance(claim.value, (int, float)) and claim.value > 0)
        return _summary_with_boundary(
            answer,
            f"已完成 {subject} 的{label}检索；覆盖 {len(risk_claims)} 项检查，其中 {positive} 项存在记录。",
            "可展开下方明细查看具体记录和来源状态。",
        )
    if primary_capability == "report":
        report_claims = [claim for claim in answer.claims if claim.dimension == "report"]
        values = _claim_values(report_claims)
        data = _latest_tool_data(outcomes, {"generate_report"})
        report_format = values.get("format", data.get("format"))
        if not report_format:
            return f"已执行 {subject} 的风险报告生成请求，但当前没有返回可下载的报告结果。"
        size = values.get("size_bytes", data.get("size_bytes"))
        length = values.get("length", data.get("length"))
        detail = f"格式：{report_format}"
        if isinstance(size, (int, float)):
            detail += f"，文件大小 {int(size)} 字节"
        elif isinstance(length, (int, float)):
            detail += f"，内容长度 {int(length)} 字符"
        return _summary_with_boundary(answer, f"已完成 {subject} 的风险报告生成；{detail}。", "可在报告入口下载并继续复核明细。")
    if primary_capability == "sourcing":
        sourcing_claims = [claim for claim in answer.claims if claim.dimension == "sourcing"]
        values = _claim_values(sourcing_claims)
        data = _latest_tool_data(outcomes, {"discover_supplier_candidates", "search_suppliers", "find_alternatives", "list_formal_suppliers"})
        requirement_status = str(current_task.get("requirement_status") or "")
        if requirement_status == "clarification_required":
            missing = [
                str(item).strip()
                for item in current_task.get("requirement_missing", [])
                if str(item).strip()
            ]
            missing_text = "、".join(missing) or "采购品类"
            return f"我还缺少{missing_text}，暂未执行寻源检索；请补充后再试。"
        local_candidates = data.get("local_candidates") if isinstance(data.get("local_candidates"), list) else []
        external_candidates = data.get("external_candidates") if isinstance(data.get("external_candidates"), list) else []
        all_candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
        result_candidates = data.get("results") if isinstance(data.get("results"), list) else []
        formal_items = data.get("items") if isinstance(data.get("items"), list) else []
        count = values.get("alternatives_count", data.get("alternatives_count"))
        if all_candidates and not local_candidates:
            external_names = {
                str(item.get("supplier_name") or item.get("company_name") or "").strip()
                for item in external_candidates
                if isinstance(item, dict)
            }
            local_candidates = [
                item for item in all_candidates
                if isinstance(item, dict)
                and str(item.get("supplier_name") or item.get("company_name") or "").strip() not in external_names
            ]
        if local_candidates or external_candidates:
            return _summary_with_boundary(
                answer,
                f"已找到 {len(local_candidates) + len(external_candidates)} 家寻源候选，其中历史合作 {len(local_candidates)} 家、外部待核验 {len(external_candidates)} 家。",
                "下面按来源分组展示匹配品类、主营产品和下一步核验事项。",
            )
        if result_candidates or formal_items:
            return _summary_with_boundary(
                answer,
                f"已找到 {len(result_candidates) or len(formal_items)} 家供应商候选。",
                "下面按来源展示主营品类、主营产品和当前状态。",
            )
        if not isinstance(count, (int, float)):
            requirement = data.get("requirement") if isinstance(data.get("requirement"), dict) else {}
            category = str(requirement.get("category") or requirement.get("product") or "当前品类").strip()
            if data.get("local_failure_reason"):
                return f"已按“{category}”发起寻源，但本地供应商库暂时不可用，当前没有可用候选；请稍后重试。"
            if data.get("external_failure_reasons") or str(data.get("external_status") or "") in {"failed", "unavailable"}:
                return f"已按“{category}”完成本地检索，但外部候选来源暂时不可用，当前没有可用候选；可稍后重试或补充内部供应商资料。"
            return f"已按“{category}”完成历史合作和外部候选检索，当前没有匹配候选；可以补充规格、交付地区或扩大候选来源。"
        return _summary_with_boundary(answer, f"已完成 {subject} 的替代供应商分析；找到 {int(count)} 家候选。", "下面展示候选来源、主营产品和当前核验状态。")
    if primary_capability == "company_profile":
        profile_claims = [claim for claim in answer.claims if claim.dimension == "company_profile"]
        if not profile_claims:
            return f"已完成 {subject} 的工商资料检索，但当前没有可验证的工商资料结果。"
        return _summary_with_boundary(answer, f"已完成 {subject} 的工商资料检索；已覆盖 {len(profile_claims)} 项资料域结果。", "可继续查看注册信息、股东、变更和分支机构明细。")
    if primary_capability == "identity_review":
        identity_claims = [claim for claim in answer.claims if claim.dimension == "identity_review"]
        if not identity_claims:
            return f"已完成 {subject} 的主体信息检索，但当前没有可验证的主体结果。"
        return _summary_with_boundary(answer, f"已完成 {subject} 的企业主体信息检索，找到 {len(identity_claims)} 条主体核验结果。", "仍需结合监控对象绑定流程确认正式主体。")
    if primary_capability == "risk_comparison":
        comparison_claims = [claim for claim in answer.claims if claim.dimension == "risk_comparison"]
        values = _claim_values(comparison_claims)
        data = _latest_tool_data(outcomes, {"compare_companies"})
        count = values.get("count", data.get("count"))
        count_text = f"共 {int(count)} 家企业" if isinstance(count, (int, float)) else "已完成企业横向比较"
        return _summary_with_boundary(answer, f"已完成企业风险对比；{count_text}。", "可结合下方指标比较各企业差异。")
    if primary_capability in {"quality", "delivery"}:
        operational_claims = [claim for claim in answer.claims if claim.dimension == primary_capability]
        values = _claim_values(operational_claims)
        label = "质量" if primary_capability == "quality" else "交付"
        if not operational_claims:
            return f"已完成 {subject} 的{label}风险查询，但当前没有可验证的{label}数据。"
        detail = []
        if values.get("risk_score") is not None:
            detail.append(f"评分 {values['risk_score']}/100")
        if values.get("risk_level") is not None:
            detail.append(f"等级：{values['risk_level']}")
        return _summary_with_boundary(answer, f"已完成 {subject} 的{label}风险分析；" + "，".join(detail) + "。", f"可结合{label}明细和时间趋势继续核查。")
    if {"lookup_company_news", "sentiment_analysis"}.intersection(tool_names):
        sentiment_claims = [claim for claim in answer.claims if claim.dimension in {"sentiment", "news"}]
        if not sentiment_claims:
            return "本轮已完成舆情检索，但当前没有可验证的新闻或负面信息结果，因此暂不下舆情结论。"
    if {"lookup_legal_risk", "lookup_business_risk"}.intersection(tool_names):
        requested_claims = [
            claim for claim in answer.claims
            if claim.dimension in {"legal_risk", "business_risk"}
            or (claim.fact_path or "").startswith("risk_detail.")
        ]
        if not requested_claims:
            return "本轮已完成司法/经营风险检索，但当前没有可验证的明细结果，暂不下确定性结论。"
    prediction_claims = [
        claim for claim in answer.claims
        if claim.dimension == "risk_prediction"
    ]
    if prediction_claims:
        subject = "、".join(dict.fromkeys(target_names[:3])) or "该供应商"
        values = {
            str(claim.fact_path): claim.value
            for claim in answer.claims
            if claim.fact_path and claim.value is not None
        }
        probability_labels = {
            "high": "高概率恶化",
            "medium": "可能恶化",
            "low": "大概率稳定",
            "unknown": "当前未覆盖",
        }
        probability = str(values.get("probability") or "unknown")
        prediction_label = str(values.get("label") or probability_labels.get(probability, "当前未覆盖"))
        prediction_has_data = values.get("has_data") is not False and probability != "unknown"
        summary_parts = [f"已完成 {subject} 未来 6-12 个月风险趋势预测"]
        if not prediction_has_data:
            summary_parts.append("当前预测数据不足，暂无法可靠判断风险恶化概率")
        else:
            summary_parts.append(f"风险恶化判断：{prediction_label}")
            warning_score = values.get("warning_score")
            max_score = values.get("max_score")
            if isinstance(warning_score, (int, float)) and isinstance(max_score, (int, float)):
                summary_parts.append(f"预警分数 {warning_score:g}/{max_score:g}")
            signal_summary = str(values.get("prediction_signal_summary") or "").strip()
            if signal_summary:
                summary_parts.append("主要信号：" + signal_summary)
        result = "；".join(summary_parts) + "。"
        if answer.status in {"partial", "needs_review"} or answer.limitations:
            return result + "以上预测仅基于已取得资料，部分维度尚未覆盖；采购动作请先按下方提示核实。"
        return result + "可结合当前风险复核结果安排后续采购动作。"
    network_claims = [
        claim for claim in answer.claims
        if claim.dimension == "risk_network"
    ]
    if "contagion_analysis" in tool_names or network_claims:
        subject = "、".join(dict.fromkeys(target_names[:3])) or "该供应商"
        if not network_claims:
            return f"已完成 {subject} 的供应链关系与传染风险检索，但当前没有可验证的关联主体或传染路径结论。"
        values = {
            str(claim.fact_path): claim.value
            for claim in network_claims
            if claim.fact_path and claim.value is not None
        }
        summary_parts = [f"已完成 {subject} 的供应链关系与传染风险分析"]
        related_count = values.get("related_count")
        branch_count = values.get("branch_count")
        dependency_count = values.get("dependency_count")
        same_industry_count = values.get("same_industry_count")
        high_risk_count = values.get("high_risk_related_count")
        if isinstance(related_count, (int, float)):
            summary_parts.append(f"识别到关联主体 {int(related_count)} 家")
        if isinstance(branch_count, (int, float)) and branch_count > 0:
            summary_parts.append(f"其中分支机构 {int(branch_count)} 家")
        if isinstance(dependency_count, (int, float)) and dependency_count > 0:
            summary_parts.append(f"供应链依赖 {int(dependency_count)} 条")
        if isinstance(same_industry_count, (int, float)) and same_industry_count > 0:
            summary_parts.append(f"同行业关联 {int(same_industry_count)} 家")
        if isinstance(high_risk_count, (int, float)):
            summary_parts.append(
                f"高风险关联主体 {int(high_risk_count)} 家"
                if high_risk_count > 0
                else "当前未发现高风险关联主体"
            )
        result = "；".join(summary_parts) + "。"
        if answer.status in {"partial", "needs_review"} or answer.limitations:
            return result + "以上判断仅基于当前已取得的关联、供应链和监控数据，未覆盖的关系不代表不存在；采购动作请先核实。"
        return result + "可结合下方关联实体和关系图进一步核查潜在传导路径。"
    if target_names and answer.claims:
        # Put the procurement conclusion before the evidence table.  Every
        # sentence below is assembled from validated Claims, so this remains a
        # deterministic presentation layer rather than an unsupported LLM
        # interpretation.
        subject = "、".join(dict.fromkeys(target_names[:3]))
        values = {
            str(claim.fact_path): claim.value
            for claim in answer.claims
            if claim.fact_path and claim.value is not None
        }
        risk_score = values.get("risk_score")
        risk_level = values.get("risk_level")
        coverage_ratio = values.get("risk_detail.data_coverage.coverage_ratio")
        coverage_limited = (
            answer.status in {"partial", "needs_review"}
            or bool(answer.limitations)
            or (isinstance(coverage_ratio, (int, float)) and coverage_ratio < 1)
        )
        summary_parts: list[str] = [f"已完成 {subject} 的供应商复核"]
        if risk_score is not None or risk_level is not None:
            risk_text = []
            if risk_score is not None:
                risk_text.append(f"综合安全评分 {risk_score}/100（分数越高风险越低）")
            if risk_level is not None:
                risk_text.append(
                    f"在已取得资料范围内{risk_level}"
                    if coverage_limited else str(risk_level)
                )
            summary_parts.append("，".join(risk_text))
        if "financial" in dimensions:
            profit_growth = values.get("net_profit_growth")
            if isinstance(profit_growth, (int, float)) and profit_growth < 0:
                summary_parts.append(f"净利润同比下降 {abs(profit_growth) * 100:.1f}%")
            elif isinstance(profit_growth, (int, float)):
                summary_parts.append(f"净利润同比增长 {profit_growth * 100:.1f}%")
            if any("financial" in str(item).lower() or "财务" in str(item) for item in answer.limitations):
                summary_parts.append("财务数据暂未覆盖")
        lawsuit_count = values.get("risk_detail.lawsuit_count")
        major_lawsuit = values.get("risk_detail.major_lawsuit")
        penalty_count = values.get("risk_detail.administrative_penalty_count")
        if isinstance(lawsuit_count, (int, float)) and lawsuit_count > 0:
            summary_parts.append(f"已发现诉讼记录 {int(lawsuit_count)} 起")
        if major_lawsuit is True:
            summary_parts.append("存在重大诉讼标记")
        if isinstance(penalty_count, (int, float)) and penalty_count > 0:
            summary_parts.append(f"行政处罚 {int(penalty_count)} 条")
        if "business_risk" in dimensions:
            missing_months = values.get("missing_month_count")
            mismatch_months = values.get("settlement_without_receipts_month_count")
            if isinstance(missing_months, (int, float)) and missing_months > 0:
                summary_parts.append(f"近 12 个月缺失交易 {int(missing_months)} 个月")
            if isinstance(mismatch_months, (int, float)) and mismatch_months > 0:
                summary_parts.append(f"结算与收货记录不一致 {int(mismatch_months)} 个月")
        result = "；".join(summary_parts) + "。"
        if coverage_limited:
            return result + "以上结论仅适用于已取得资料范围，部分维度尚未覆盖；采购动作请先按下方提示核实。"
        return result + "可结合下方数据依据安排后续采购动作。"
    if "sourcing" in dimensions or "discover_supplier_candidates" in tool_names or "search_suppliers" in tool_names:
        candidate_count = 0
        external_count = 0
        merged_candidate_count = 0
        merged_contains_external = False
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            data = outcome.get("data")
            if not isinstance(data, dict):
                continue
            candidates = data.get("candidates")
            if isinstance(candidates, list):
                candidate_count = max(candidate_count, len(candidates))
                merged_candidate_count = max(merged_candidate_count, len(candidates))
                merged_contains_external = merged_contains_external or any(
                    isinstance(candidate, dict)
                    and (
                        candidate.get("candidate_type") in {"external", "external_candidate"}
                        or candidate.get("source") in {"gasgoo_manual_export", "staged_external", "external"}
                        or candidate.get("status") == "staged_candidate"
                    )
                    for candidate in candidates
                )
            for key in ("external_candidates", "external"):
                value = data.get(key)
                if isinstance(value, list):
                    external_count = max(external_count, len(value))
        if candidate_count or external_count:
            # External discovery may return a merged ``candidates`` list and
            # also expose ``external_candidates`` for the UI.  In that shape,
            # adding both values double-counts the same candidates (for
            # example, 14 merged rows + 10 external rows becomes 24).
            total_count = (
                merged_candidate_count
                if merged_contains_external
                else candidate_count + external_count
            )
            return f"已找到 {total_count} 家寻源候选，其中 {external_count} 家为外部待核验候选；下面按历史合作与外部候选分开展示。"
        return "已完成历史供应商和外部候选检索，但当前没有返回可用候选。"
    if answer.status == "completed":
        return "已完成基于有效证据的 Agent 分析。"
    if answer.status == "partial":
        return "已完成部分分析，部分结果来自演示数据或覆盖不足。"
    if answer.status == "needs_review":
        return "分析已停止在证据复核点，当前不输出未经证据支持的确定性结论。"
    return "Agent 运行未完成。"


def build_harness_graph(
    *,
    executor: ToolExecutor | None = None,
    persist: PersistCallback | None = None,
    progress: ProgressCallback | None = None,
    narrate: NarrateCallback | None = None,
    checkpointer: Any = None,
) -> Any:
    """Build the only runtime graph used by Harness-level tests and callers."""

    active_executor = executor or ToolExecutor(TOOL_REGISTRY)

    async def persist_event(event: str, state: HarnessState, patch: dict[str, Any] | None = None) -> None:
        snapshot = dict(state)
        if patch:
            snapshot.update(patch)
        events = list(snapshot.get("events", []))
        events.append({"event": event, "at": utc_now_iso()})
        snapshot["events"] = events
        if persist:
            result = persist(event, snapshot)
            if inspect.isawaitable(result):
                await result
        if progress:
            result = progress(event, snapshot, True)
            if inspect.isawaitable(result):
                await result

    async def load_session(state: HarnessState) -> dict[str, Any]:
        required = ("session_id", "turn_id", "run_id", "user_message")
        missing = [key for key in required if not str(state.get(key) or "").strip()]
        if missing:
            raise ValueError(f"Harness 身份字段缺失: {', '.join(missing)}")
        budget = new_budget(state.get("budget"))
        patch = {
            "schema_version": 1,
            "budget": budget.model_dump(mode="json"),
            "tool_call_count": int(state.get("tool_call_count", 0)),
            "llm_call_count": int(state.get("llm_call_count", 0)),
            "loop_iterations": int(state.get("loop_iterations", 0)),
            "remediation_attempts": int(state.get("remediation_attempts", 0)),
            "status": "loaded",
            "started_at": state.get("started_at") or utc_now_iso(),
            "tool_outcomes": list(state.get("tool_outcomes", [])),
            "evidence_records": list(state.get("evidence_records", [])),
            "claims": list(state.get("claims", [])),
        }
        await persist_event("load_session", state, patch)
        return patch

    async def resolve_turn(state: HarnessState) -> dict[str, Any]:
        context = state.get("execution_context")
        if not isinstance(context, dict):
            raise ValueError("Harness execution_context 必须是单一已解析快照")
        current_task = state.get("current_task") or context.get("current_task") or {}
        if not isinstance(current_task, dict):
            raise ValueError("Harness current_task 契约无效")
        patch = {"current_task": dict(current_task), "status": "turn_resolved"}
        await persist_event("resolve_turn", state, patch)
        return patch

    async def build_plan(state: HarnessState) -> dict[str, Any]:
        tasks = _build_default_plan(state)
        remediation_specs = _remediation_specs(state)
        if not remediation_specs:
            remediation_specs = _build_remediation_specs(state, tasks)
        patch = {
            "task_specs": [task.model_dump(mode="json") for task in tasks],
            "remediation_specs": remediation_specs,
            "status": "planned",
        }
        await persist_event("build_plan", state, patch)
        return patch

    async def execute_ready_tasks(state: HarnessState) -> dict[str, Any]:
        budget = new_budget(state.get("budget"))
        tasks = [HarnessTask.model_validate(item) for item in state.get("task_specs", [])]
        outcomes = list(state.get("tool_outcomes", []))
        evidence_records = list(state.get("evidence_records", []))
        claims = list(state.get("claims", []))
        count = int(state.get("tool_call_count", 0))
        llm_count = int(state.get("llm_call_count", 0))
        started = _parse_time(state.get("started_at"))
        executed = 0
        stop_reason: str | None = None

        if llm_count > budget.max_llm_calls:
            stop_reason = "llm_budget_exhausted"
        while stop_reason is None:
            if all(task.status in {"completed", "partial", "failed"} for task in tasks):
                stop_reason = "all_tasks_processed"
                break
            remaining_calls = budget.max_tool_calls - count
            remaining_seconds = budget.max_duration_seconds - (
                datetime.now(timezone.utc) - started
            ).total_seconds()
            if remaining_calls <= 0:
                stop_reason = "tool_budget_exhausted"
                break
            if remaining_seconds <= 0:
                stop_reason = "deadline_exceeded"
                break
            wave = _select_task_wave(
                tasks,
                max_parallel_tasks=budget.max_parallel_tasks,
                max_tasks=remaining_calls,
            )
            if not wave:
                pending = [task for task in tasks if task.status == "pending"]
                if pending:
                    stop_reason = "dependency_blocked"
                break

            for task in wave:
                task.status = "running"

            async def execute_one(index: int, task: HarnessTask) -> ToolOutcome:
                context = ToolContext(
                    session_id=state["session_id"],
                    run_id=state["run_id"],
                    user_id=state.get("user_id"),
                    entity_id=task.entity_id,
                    monitor_target_id=(
                        str(task.arguments.get("monitor_target_id") or "").strip() or
                        _monitor_target_id(
                            str(
                                task.arguments.get("company_name")
                                or task.arguments.get("supplier_name")
                                or task.arguments.get("supplier_reference")
                                or ""
                            ),
                            state.get("execution_context") or {},
                        )
                    ),
                    tool_call_count=count + index,
                    max_tool_calls=budget.max_tool_calls,
                )
                if progress:
                    result = progress(
                        "tool_call",
                        {
                            "run_id": state["run_id"],
                            "task_id": task.task_id,
                            "tool": task.tool_name,
                            "args": task.arguments,
                            "trace": {
                                "task_id": task.task_id,
                                "input_hash": _input_hash(task.arguments),
                                "started_at": utc_now_iso(),
                            },
                        },
                        False,
                    )
                    if inspect.isawaitable(result):
                        await result
                tool_started = time.monotonic()
                outcome = await asyncio.wait_for(
                    active_executor.execute(task.tool_name, task.arguments, context),
                    timeout=max(0.001, remaining_seconds),
                )
                if progress:
                    result = progress(
                        "tool_result",
                        {
                            "run_id": state["run_id"],
                            "task_id": task.task_id,
                            "tool": task.tool_name,
                            "result": outcome.model_dump(mode="json"),
                            "trace": {
                                "task_id": task.task_id,
                                "call_id": outcome.call_id,
                                "tool_version": outcome.tool_version,
                                "duration_ms": int((time.monotonic() - tool_started) * 1000),
                            },
                        },
                        False,
                    )
                    if inspect.isawaitable(result):
                        await result
                return outcome

            wave_results = await asyncio.gather(
                *(execute_one(index, task) for index, task in enumerate(wave)),
                return_exceptions=True,
            )
            for task, result in zip(wave, wave_results, strict=True):
                if isinstance(result, asyncio.TimeoutError):
                    task.status = "failed"
                    outcome = ToolOutcome(
                        call_id=f"{state['run_id']}:{task.task_id}",
                        tool_name=task.tool_name,
                        tool_version="runtime",
                        status="unavailable",
                        error={"code": "deadline_exceeded", "message": "任务超过 Harness 截止时间", "retryable": True},
                    )
                    stop_reason = "deadline_exceeded"
                elif isinstance(result, Exception):
                    task.status = "failed"
                    outcome = ToolOutcome(
                        call_id=f"{state['run_id']}:{task.task_id}",
                        tool_name=task.tool_name,
                        tool_version="runtime",
                        status="failed",
                        error={"code": "task_execution_failed", "message": str(result), "retryable": False},
                    )
                else:
                    outcome = result
                    task.status = "completed" if outcome.status in {"success", "partial", "not_found"} else "failed"
                if isinstance(result, Exception) and progress:
                    progress_result = progress(
                        "tool_result",
                        {
                            "run_id": state["run_id"],
                            "task_id": task.task_id,
                            "tool": task.tool_name,
                            "result": outcome.model_dump(mode="json"),
                            "trace": {"task_id": task.task_id, "duration_ms": 0},
                        },
                        False,
                    )
                    if inspect.isawaitable(progress_result):
                        await progress_result
                task.attempts += 1
                outcomes.append(outcome.model_dump(mode="json"))
                executed += 1
                for record in _payload_evidence(outcome, task):
                    evidence_records.append(record.model_dump(mode="json"))
                claims.extend(claim.model_dump(mode="json") for claim in _payload_claims(outcome, task))
            count += len(wave)
            if stop_reason is not None:
                break

        if stop_reason is None and all(task.status in {"completed", "partial", "failed"} for task in tasks):
            status = "executed"
            stop_reason = "all_tasks_processed"
        else:
            status = "budget_exhausted" if stop_reason in {
                "llm_budget_exhausted", "tool_budget_exhausted", "deadline_exceeded"
            } else "blocked"
        patch = {
            "task_specs": [task.model_dump(mode="json") for task in tasks],
            "tool_outcomes": outcomes,
            "evidence_records": evidence_records,
            "claims": claims,
            "tool_call_count": count,
            "loop_exit_reason": stop_reason,
            "status": status,
        }
        await persist_event("execute_ready_tasks", state, patch)
        return patch

    async def validate_evidence(state: HarnessState) -> dict[str, Any]:
        ledger = EvidenceLedger(
            [EvidenceRecord.model_validate(item) for item in state.get("evidence_records", [])]
        )
        claims = [Claim.model_validate(item) for item in state.get("claims", [])]
        tasks = [HarnessTask.model_validate(item) for item in state.get("task_specs", [])]
        required = _required_dimensions(tasks)
        coverage = ledger.coverage(required, _required_evidence_items(tasks))
        validated = [ledger.validate_claim(claim).claim.model_dump(mode="json") for claim in claims]
        missing = list(coverage.missing_dimensions)
        patch = {
            "validated_claims": validated,
            "evidence_coverage": coverage.model_dump(mode="json"),
            "status": "evidence_validated" if not missing else "evidence_incomplete",
        }
        await persist_event("validate_evidence", state, patch)
        return patch

    def route_after_validation(state: HarnessState) -> str:
        coverage = state.get("evidence_coverage") or {}
        missing = coverage.get("missing_dimensions", [])
        attempts = int(state.get("remediation_attempts", 0))
        budget = new_budget(state.get("budget"))
        specs = _remediation_specs(state)
        existing_sources = {
            (str(item.get("source_key")), str(item.get("loop_type") or "evidence"))
            for item in state.get("task_specs", [])
            if isinstance(item, dict) and item.get("source_key")
        }
        has_new_spec = any(
            (str(item.get("source_key")), str(item.get("loop_type") or "evidence")) not in existing_sources
            for item in specs
        )
        if missing and specs and has_new_spec and attempts < budget.max_loop_iterations:
            return "remediate"
        return "render_answer"

    async def remediate(state: HarnessState) -> dict[str, Any]:
        attempts = int(state.get("remediation_attempts", 0)) + 1
        existing = [item for item in state.get("task_specs", []) if isinstance(item, dict)]
        existing_ids = {str(item.get("task_id")) for item in existing}
        existing_sources = {
            (str(item.get("source_key")), str(item.get("loop_type") or "evidence"))
            for item in existing
            if item.get("source_key")
        }
        additions = [
            item for item in _remediation_specs(state)
            if isinstance(item, dict)
            and str(item.get("task_id")) not in existing_ids
            and (
                not item.get("source_key")
                or (
                    str(item.get("source_key")),
                    str(item.get("loop_type") or "evidence"),
                ) not in existing_sources
            )
        ]
        patch = {
            "task_specs": list(state.get("task_specs", [])) + additions,
            "remediation_attempts": attempts,
            "loop_iterations": int(state.get("loop_iterations", 0)) + 1,
            "loop_exit_reason": "remediation_scheduled",
            "status": "remediating",
        }
        await persist_event("remediate", state, patch)
        return patch

    async def render_answer(state: HarnessState) -> dict[str, Any]:
        ledger = EvidenceLedger(
            [EvidenceRecord.model_validate(item) for item in state.get("evidence_records", [])]
        )
        claims = [Claim.model_validate(item) for item in state.get("claims", [])]
        tasks = [HarnessTask.model_validate(item) for item in state.get("task_specs", [])]
        answer = build_agent_answer(
            summary="待生成",
            ledger=ledger,
            claims=claims,
            required_dimensions=_required_dimensions(tasks),
            required_evidence=_required_evidence_items(tasks),
        )
        if _requests_procurement_action(state):
            answer = answer.model_copy(update={"action_proposals": _procurement_action_proposals(answer)})
        if not claims:
            no_plan = not tasks
            summary = _no_plan_summary(state) if no_plan else _summary(answer, state)
            answer = answer.model_copy(
                update={
                    "status": "needs_review",
                    "summary": summary,
                    "limitations": list(dict.fromkeys([
                        *answer.limitations,
                        "当前请求未形成可执行任务" if no_plan else "工具结果未提供可验证 Claim",
                    ])),
                }
            )
        else:
            answer = answer.model_copy(update={"summary": _summary(answer, state)})
        narration_attempted = False
        budget = new_budget(state.get("budget"))
        if (
            narrate is not None
            and answer.status != "failed"
            and int(state.get("llm_call_count", 0)) < budget.max_llm_calls
            and answer.claims
        ):
            narration_attempted = True
            try:
                narrative = narrate(answer, str(state.get("user_message") or ""))
                if inspect.isawaitable(narrative):
                    narrative = await narrative
                if isinstance(narrative, AgentAnswer):
                    answer = narrative
            except Exception as exc:
                # Narration is an optional presentation layer.  The validated
                # deterministic summary remains the source of truth.
                logger.warning("answer_narration_callback_failed", error=str(exc))
        coverage = state.get("evidence_coverage") or {}
        loop_exit_reason = state.get("loop_exit_reason")
        if coverage.get("missing_dimensions") and loop_exit_reason not in {
            "llm_budget_exhausted",
            "tool_budget_exhausted",
            "deadline_exceeded",
        }:
            if int(state.get("remediation_attempts", 0)) >= new_budget(state.get("budget")).max_loop_iterations:
                loop_exit_reason = "iteration_budget_exhausted"
            else:
                existing_sources = {
                    (str(item.get("source_key")), str(item.get("loop_type") or "evidence"))
                    for item in state.get("task_specs", [])
                    if isinstance(item, dict) and item.get("source_key")
                }
                has_new_spec = any(
                    (str(item.get("source_key")), str(item.get("loop_type") or "evidence")) not in existing_sources
                    for item in _remediation_specs(state)
                )
                if not has_new_spec:
                    loop_exit_reason = "no_remediation_spec"
        patch = {
            "answer": answer.model_dump(mode="json"),
            "loop_exit_reason": loop_exit_reason,
            "status": answer.status,
        }
        if narration_attempted:
            patch["llm_call_count"] = int(state.get("llm_call_count", 0)) + 1
        await persist_event("render_answer", state, patch)
        return patch

    async def persist_turn(state: HarnessState) -> dict[str, Any]:
        await persist_event("persist_turn", state)
        return {"status": state.get("status", "needs_review")}

    graph = StateGraph(HarnessState)
    graph.add_node("load_session", load_session)
    graph.add_node("resolve_turn", resolve_turn)
    graph.add_node("build_plan", build_plan)
    graph.add_node("execute_ready_tasks", execute_ready_tasks)
    graph.add_node("validate_evidence", validate_evidence)
    graph.add_node("remediate", remediate)
    graph.add_node("render_answer", render_answer)
    graph.add_node("persist_turn", persist_turn)
    graph.add_edge(START, "load_session")
    graph.add_edge("load_session", "resolve_turn")
    graph.add_edge("resolve_turn", "build_plan")
    graph.add_edge("build_plan", "execute_ready_tasks")
    graph.add_edge("execute_ready_tasks", "validate_evidence")
    graph.add_conditional_edges(
        "validate_evidence",
        route_after_validation,
        {"remediate": "remediate", "render_answer": "render_answer"},
    )
    graph.add_edge("remediate", "execute_ready_tasks")
    graph.add_edge("render_answer", "persist_turn")
    graph.add_edge("persist_turn", END)
    return graph.compile(checkpointer=checkpointer) if checkpointer is not None else graph.compile()


def _no_plan_summary(state: HarnessState) -> str:
    """Explain missing intent/data instead of mislabeling it as an empty finding."""
    message = str((state.get("current_task") or {}).get("user_message") or state.get("user_message") or "")
    if any(token in message for token in ("推荐供应商", "寻找供应商", "找供应商")):
        return "我还不能开始寻源：请补充物料号、零件名称或采购品类。"
    if any(token in message for token in ("趋势", "变化", "复核")):
        return "我还不能形成复核结论：请指定正式供应商或明确的监控范围。"
    return "我暂未识别出可执行的分析任务，请补充供应商、监控范围或具体问题。"


def _requests_procurement_action(state: HarnessState) -> bool:
    message = str((state.get("current_task") or {}).get("user_message") or state.get("user_message") or "")
    return any(token in message for token in ("采购动作", "采取动作", "下一步怎么做", "是否需要处理", "要不要处理"))


def _procurement_action_proposals(answer: AgentAnswer) -> list[dict[str, Any]]:
    """Produce a read-only next-action recommendation from validated evidence."""
    if answer.limitations:
        return [{
            "action_type": "supplement_data",
            "label": "当前不建议变更采购策略",
            "reason": "系统已完成现有数据核验，但部分维度当前未覆盖，不能直接做暂停或切换供应商的决定。",
            "requires_approval": False,
        }]
    risky = any(
        any(token in claim.statement for token in ("下降", "恶化", "风险", "不一致", "缺失"))
        for claim in answer.claims
    )
    if risky:
        return [{
            "action_type": "procurement_review",
            "label": "安排采购复核",
            "reason": "已发现需要人工确认的风险信号，先核实证据再决定是否调整采购策略。",
            "requires_approval": False,
        }]
    return [{
        "action_type": "continue_monitoring",
        "label": "继续观察，暂不执行采购变更",
        "reason": "当前证据未支持立即暂停、替换或扩大采购的决定。",
        "requires_approval": False,
    }]


async def run_harness(
    state: HarnessState,
    *,
    executor: ToolExecutor | None = None,
    persist: PersistCallback | None = None,
    progress: ProgressCallback | None = None,
    narrate: NarrateCallback | None = None,
    checkpointer: Any = None,
    config: dict[str, Any] | None = None,
) -> HarnessState:
    """Invoke the unified graph with the required LangGraph thread identity."""
    active_config = dict(config or {})
    configurable = dict(active_config.get("configurable") or {})
    configurable.setdefault("thread_id", state["run_id"])
    active_config["configurable"] = configurable
    graph = build_harness_graph(
        executor=executor,
        persist=persist,
        progress=progress,
        narrate=narrate,
        checkpointer=checkpointer,
    )
    return await graph.ainvoke(state, config=active_config)


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


__all__ = ["build_harness_graph", "run_harness"]
