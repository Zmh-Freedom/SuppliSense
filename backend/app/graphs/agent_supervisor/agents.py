"""Bounded, read-only sub-agent adapters and ready-task scheduling."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.graphs.agent_supervisor.contracts import (
    AgentError,
    AgentMetrics,
    AgentResult,
    PlannerTask,
    TaskPlan,
)
from app.graphs.agent_supervisor.state import AgentTaskState


@dataclass(frozen=True)
class AgentTaskContext:
    """Read-only input passed to a sub-agent adapter.

    The context deliberately carries data only: handlers never receive service
    callables and therefore cannot be handed a mutation-capable operation.
    """

    task: PlannerTask
    run_id: str
    user_query: str
    intent: dict[str, Any]
    dependency_results: dict[str, AgentResult]


AgentHandler = Callable[[AgentTaskContext], Awaitable[AgentResult]]


def _company_name(context: AgentTaskContext) -> str | None:
    value = context.intent.get("company_name")
    return value if isinstance(value, str) and value.strip() else None


def _sourcing_evidence(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": f"supplier:{candidate.get('supplier_id') or index}",
            "source": "本地供应商主数据",
            "source_type": "internal",
            "freshness": "fresh",
            "confidence": 0.9,
            "company_id": str(candidate.get("supplier_id") or "") or None,
            "dimension": "sourcing",
            "claim": f"匹配供应商：{candidate.get('supplier_name') or '未命名供应商'}",
            "metadata": {"supplier": candidate},
        }
        for index, candidate in enumerate(candidates)
    ]


def _risk_evidence(company_name: str, risk_info: Any) -> list[dict[str, Any]]:
    if risk_info is None:
        return []
    payload = risk_info.model_dump() if hasattr(risk_info, "model_dump") else dict(risk_info)
    return [{
        "evidence_id": f"risk:{company_name}",
        "source": "本地企业风险记录",
        "source_type": "internal",
        "freshness": "fresh",
        "confidence": 0.85,
        "company_id": company_name,
        "dimension": "risk",
        "claim": f"已读取 {company_name} 的本地风险记录",
        "metadata": {"risk": payload},
    }]


async def _run_sourcing(context: AgentTaskContext) -> AgentResult:
    """Search the local supplier library through its read-only V2 boundary."""
    requirement = context.intent.get("requirement")
    if not isinstance(requirement, dict):
        return AgentResult(
            agent="sourcing",
            status="needs_review",
            summary="缺少可执行的采购需求，等待补充结构化条件。",
        )

    from app.domains.sourcing_risk.discovery_service import discover_local_candidates

    candidates = await asyncio.to_thread(discover_local_candidates, requirement, {})
    evidence = _sourcing_evidence(candidates)
    return AgentResult(
        agent="sourcing",
        status="completed" if evidence else "needs_review",
        summary=f"已从本地供应商库检索到 {len(candidates)} 个候选供应商。",
        evidence=evidence,
        metrics=AgentMetrics(evidence_count=len(evidence)),
    )


async def _run_risk(context: AgentTaskContext) -> AgentResult:
    """Read existing risk records without invoking snapshot-producing assessment."""
    from app.domains.risk.repo_company import get_risk_info

    company_name = _company_name(context)
    sourcing = context.dependency_results.get("sourcing")
    candidates = [
        item.metadata.get("supplier", {})
        for item in (sourcing.evidence if sourcing else [])
        if item.metadata.get("supplier")
    ]
    if company_name is None and len(candidates) == 1:
        company_name = str(candidates[0].get("supplier_name") or "") or None
    if company_name is None:
        if not candidates:
            return AgentResult(agent="risk", status="needs_review", summary="缺少待评估供应商名称。")
        risk_records = []
        for candidate in candidates:
            name = str(candidate.get("supplier_name") or "").strip()
            if name:
                risk_records.append((name, await asyncio.to_thread(get_risk_info, name)))
        evidence = [item for name, info in risk_records for item in _risk_evidence(name, info)]
        return AgentResult(
            agent="risk",
            status="completed" if evidence else "needs_review",
            summary=f"已读取 {len(evidence)} 个候选供应商的本地风险记录。" if evidence else "候选供应商暂无本地风险记录。",
            evidence=evidence,
            metrics=AgentMetrics(evidence_count=len(evidence)),
        )

    risk_info = await asyncio.to_thread(get_risk_info, company_name)
    summary = "未找到本地风险记录。" if risk_info is None else "已读取本地风险记录。"
    return AgentResult(
        agent="risk",
        status="completed" if risk_info is not None else "needs_review",
        summary=summary,
        evidence=_risk_evidence(company_name, risk_info),
    )


async def _run_compliance(context: AgentTaskContext) -> AgentResult:
    """Call the existing sanctions read boundary without any remediation action."""
    company_name = _company_name(context)
    if company_name is None:
        return AgentResult(agent="compliance", status="needs_review", summary="缺少待筛查供应商名称。")

    from app.domains.risk.sanctions_service import check_sanctions

    result = await asyncio.to_thread(check_sanctions, company_name)
    summary = "未命中制裁或黑名单记录。" if result.get("clean") else "发现制裁或合规风险记录。"
    return AgentResult(agent="compliance", status="completed", summary=summary)


async def _run_sentiment(context: AgentTaskContext) -> AgentResult:
    """Read cached sentiment only; never refresh news or create an alert."""
    company_name = _company_name(context)
    if company_name is None:
        return AgentResult(agent="sentiment", status="needs_review", summary="缺少待分析供应商名称。")

    from app.domains.risk.sentiment import _get_cached_sentiment

    result = await asyncio.to_thread(_get_cached_sentiment, company_name)
    summary = "未找到已缓存的舆情结果。" if result is None else "已读取缓存的舆情结果。"
    return AgentResult(agent="sentiment", status="completed", summary=summary)


AGENT_HANDLERS: dict[str, AgentHandler] = {
    "sourcing": _run_sourcing,
    "risk": _run_risk,
    "compliance": _run_compliance,
    "sentiment": _run_sentiment,
}


def _retryable_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    module = exc.__class__.__module__.lower()
    return isinstance(exc, (TimeoutError, ConnectionError, OSError)) or any(
        marker in name or marker in module
        for marker in ("timeout", "provider", "connection", "apierror")
    )


def _failed_result(task: PlannerTask, exc: Exception, attempts: int, started_at: float) -> AgentResult:
    return AgentResult(
        agent=task.agent,
        status="failed",
        summary=f"{task.agent} 子 Agent 执行失败。",
        error=AgentError(
            code=exc.__class__.__name__,
            message=str(exc) or exc.__class__.__name__,
            retryable=_retryable_error(exc),
        ),
        metrics=AgentMetrics(
            duration_ms=round((time.monotonic() - started_at) * 1000),
            attempts=attempts,
        ),
    )


async def run_agent_task(task: PlannerTask, state: AgentTaskState) -> AgentResult:
    """Execute one adapter with at most one retry for provider/timeout failures."""
    previous = _result_map(state)
    context = AgentTaskContext(
        task=task,
        run_id=state.get("run_id", ""),
        user_query=state.get("user_query", ""),
        intent=dict(state.get("intent", {})),
        dependency_results={dependency: previous[dependency] for dependency in task.depends_on if dependency in previous},
    )
    handler = AGENT_HANDLERS[task.agent]
    started_at = time.monotonic()

    for attempts in (1, 2):
        try:
            result = await handler(context)
            return result.model_copy(update={
                "metrics": AgentMetrics(
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                    evidence_count=len(result.evidence),
                    attempts=attempts,
                )
            })
        except Exception as exc:
            if attempts == 1 and _retryable_error(exc):
                continue
            return _failed_result(task, exc, attempts, started_at)

    raise RuntimeError("unreachable")


def _result_map(state: AgentTaskState) -> dict[str, AgentResult]:
    raw_results = state.get("agent_results", {})
    results: dict[str, AgentResult] = {}
    for task_id, raw_result in raw_results.items():
        results[task_id] = raw_result if isinstance(raw_result, AgentResult) else AgentResult.model_validate(raw_result)
    return results


def _ready_tasks(plan: TaskPlan, state: AgentTaskState) -> list[PlannerTask]:
    results = _result_map(state)
    return [
        task
        for task in plan.tasks
        if task.task_id not in results
        and all(results.get(dependency, None) and results[dependency].status == "completed" for dependency in task.depends_on)
    ]


async def run_ready_tasks(plan: TaskPlan, state: AgentTaskState) -> dict[str, AgentResult]:
    """Run all dependency-ready tasks concurrently and preserve every outcome."""
    tasks = _ready_tasks(plan, state)
    outcomes = await asyncio.gather(
        *(run_agent_task(task, state) for task in tasks), return_exceptions=True
    )
    results: dict[str, AgentResult] = {}
    for task, outcome in zip(tasks, outcomes, strict=True):
        if isinstance(outcome, Exception):
            results[task.task_id] = _failed_result(task, outcome, 1, time.monotonic())
        else:
            results[task.task_id] = outcome
    return results
