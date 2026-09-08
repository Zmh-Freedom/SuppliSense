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
    supplier_references: list[dict[str, Any]] | None = None


AgentHandler = Callable[[AgentTaskContext], Awaitable[AgentResult]]


def _company_name(context: AgentTaskContext) -> str | None:
    value = context.intent.get("company_name")
    return value if isinstance(value, str) and value.strip() else None


def _company_names(context: AgentTaskContext) -> list[str]:
    """Resolve all structured targets, preserving their conversation order."""
    raw_names = context.intent.get("target_supplier_names")
    names = raw_names if isinstance(raw_names, list) else []
    if not names:
        current_task = context.intent.get("current_task")
        if isinstance(current_task, dict):
            target_names = current_task.get("target_supplier_names")
            names = target_names if isinstance(target_names, list) else []
    if not names:
        single_name = _company_name(context)
        names = [single_name] if single_name else []
    if not names:
        sourcing = context.dependency_results.get("sourcing")
        names = [
            item.metadata.get("supplier", {}).get("supplier_name")
            for item in (sourcing.evidence if sourcing else [])
        ]
    return list(dict.fromkeys(
        name.strip() for name in names if isinstance(name, str) and name.strip()
    ))


def _target_reference(context: AgentTaskContext, company_name: str) -> dict[str, Any]:
    """Resolve the stable monitor identity retained for one supplier mention."""
    references = context.supplier_references or []
    for reference in references:
        if isinstance(reference, dict) and reference.get("name") == company_name:
            return reference
    return {}


def _target_fields(context: AgentTaskContext, company_name: str) -> dict[str, Any]:
    reference = _target_reference(context, company_name)
    return {
        field: reference[field]
        for field in ("monitor_target_id", "target_type")
        if reference.get(field)
    }


def _sourcing_evidence(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence = []
    for index, candidate in enumerate(candidates):
        candidate_type = candidate.get("candidate_type")
        source_stage = candidate.get("source_stage")
        if candidate_type == "external" or source_stage == "external":
            source = "外部联网/天眼查待核验候选"
            source_type = "third_party"
            confidence = 0.65
        elif source_stage == "feishu_formal":
            source = "飞书正式供应商快照"
            source_type = "internal"
            confidence = 0.9
        else:
            source = "本地供应商历史数据"
            source_type = "internal"
            confidence = 0.85
        evidence.append({
            "evidence_id": f"supplier:{candidate.get('candidate_id') or candidate.get('supplier_id') or index}",
            "source": source,
            "source_type": source_type,
            "freshness": "fresh",
            "confidence": confidence,
            "monitor_target_id": candidate.get("monitor_target_id"),
            "target_type": candidate.get("target_type") or (
                "external_candidate" if candidate.get("candidate_id") and candidate_type == "external"
                else "formal_supplier" if candidate.get("supplier_id") else None
            ),
            "company_id": str(candidate.get("supplier_id") or "") or None,
            "dimension": "sourcing",
            "claim": f"匹配供应商：{candidate.get('supplier_name') or '未命名供应商'}",
            "metadata": {"supplier": candidate},
        })
    return evidence


def _risk_evidence(
    company_name: str,
    assessment: Any,
    *,
    target_fields: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if assessment is None:
        return []
    payload = assessment.model_dump() if hasattr(assessment, "model_dump") else dict(assessment)
    coverage = (payload.get("risk_detail") or {}).get("data_coverage") or {}
    scope = "完整" if coverage.get("assessment_status") == "complete" else "初步"
    coverage_ratio = float(coverage.get("coverage_ratio") or 0)
    claim = (
        f"{company_name} 综合风险评分：{payload.get('risk_score', '-')} / 100，"
        f"等级：{payload.get('risk_level', '未知')}。"
        if scope == "完整"
        else (
            f"{company_name} 初步风险评分：{payload.get('risk_score', '-')} / 100，"
            f"数据覆盖度 {coverage_ratio:.0%}，不足以形成综合风险结论"
            f"（模型原始等级：{payload.get('risk_level', '未知')}）。"
        )
    )
    return [{
        "evidence_id": f"risk:{company_name}",
        "source": "V2 可解释风险评分",
        "source_type": "internal",
        "freshness": "fresh",
        "confidence": 0.85 if scope == "完整" else 0.65,
        **(target_fields or {}),
        "company_id": company_name,
        "dimension": "risk",
        "claim": claim,
        "metadata": {"risk": payload},
    }]


def _evidence(
    company_name: str,
    dimension: str,
    source: str,
    claim: str,
    payload: dict[str, Any],
    *,
    freshness: str = "fresh",
    confidence: float = 0.8,
    target_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "evidence_id": f"{dimension}:{company_name}",
        "source": source,
        "source_type": "internal",
        "freshness": freshness,
        "confidence": confidence,
        **(target_fields or {}),
        "company_id": company_name,
        "dimension": dimension,
        "claim": claim,
        "metadata": payload,
    }


def _risk_level(value: str | None) -> str:
    normalized = str(value or "").lower()
    if normalized in {"critical", "严重", "极高风险"}:
        return "critical"
    if normalized in {"high", "高风险"}:
        return "high"
    if normalized in {"medium", "中风险"}:
        return "medium"
    if normalized in {"low", "低风险"}:
        return "low"
    return "unknown"


async def _run_sourcing(context: AgentTaskContext) -> AgentResult:
    """Search the local supplier library through its read-only V2 boundary."""
    requirement = context.intent.get("requirement")
    if not isinstance(requirement, dict):
        return AgentResult(
            agent="sourcing",
            status="needs_review",
            summary="缺少可执行的采购需求，等待补充结构化条件。",
        )

    from app.domains.sourcing_risk.discovery_service import discover_candidates

    discovery = await asyncio.to_thread(
        discover_candidates, requirement, {"minimum_candidate_count": 3}
    )
    if not isinstance(discovery, dict):
        return AgentResult(
            agent="sourcing",
            status="failed",
            summary="寻源发现服务返回格式无效，无法生成可追溯推荐。",
        )
    candidates = [*discovery.get("local_candidates", []), *discovery.get("external_candidates", [])]
    evidence = _sourcing_evidence(candidates)
    source = discovery.get("source")
    if source == "local_and_external":
        summary = f"已检索本地/飞书正式供应商，并补充 {len(discovery.get('external_candidates', []))} 个外部待核验候选。"
    elif source == "local":
        summary = f"已从本地/飞书正式供应商快照检索到 {len(candidates)} 个候选供应商。"
    else:
        summary = f"寻源阶段未形成可用候选，共返回 {len(candidates)} 个结果。"
    return AgentResult(
        agent="sourcing",
        status="completed" if evidence else "needs_review",
        summary=summary,
        evidence=evidence,
        metrics=AgentMetrics(evidence_count=len(evidence)),
    )


async def _run_risk(context: AgentTaskContext) -> AgentResult:
    """Calculate a read-only V2 risk preview without creating snapshots."""
    from app.domains.risk.service import calculate_company_risk_preview

    company_names = _company_names(context)
    if not company_names:
        return AgentResult(agent="risk", status="needs_review", summary="缺少待评估供应商名称。")
    risk_records = [
        (name, await asyncio.to_thread(calculate_company_risk_preview, name))
        for name in company_names
    ]
    evidence = [
        item for name, risk_info in risk_records
        for item in _risk_evidence(name, risk_info, target_fields=_target_fields(context, name))
    ]
    findings = [
        {
            "type": "risk",
            "level": (
                _risk_level(getattr(risk_info, "risk_level", None))
                if (getattr(risk_info, "risk_detail", None) or {}).get("data_coverage", {}).get("assessment_status")
                == "complete"
                else "unknown"
            ),
            "title": f"{name} 综合风险",
            "description": f"V2 评分 {getattr(risk_info, 'risk_score', '-')} / 100，" + (
                f"当前等级：{getattr(risk_info, 'risk_level', '未知')}"
                if (getattr(risk_info, "risk_detail", None) or {}).get("data_coverage", {}).get("assessment_status")
                == "complete"
                else "数据覆盖不足，不能形成综合风险结论"
            ),
            "confidence": 0.85 if (
                (getattr(risk_info, "risk_detail", None) or {}).get("data_coverage", {}).get("assessment_status")
                == "complete"
            ) else 0.65,
            "evidence_ids": [f"risk:{name}"],
        }
        for name, risk_info in risk_records if risk_info is not None
    ]
    return AgentResult(
        agent="risk",
        status=(
            "completed"
            if len(evidence) == len(company_names)
            and all(
                (getattr(result, "risk_detail", None) or {}).get("data_coverage", {}).get("assessment_status")
                == "complete"
                for _, result in risk_records if result is not None
            )
            else "needs_review"
        ),
        summary=f"已按 V2 评分模型完成 {len(evidence)}/{len(company_names)} 家供应商的风险预览。",
        evidence=evidence,
        findings=findings,
        metrics=AgentMetrics(evidence_count=len(evidence)),
    )


async def _run_compliance(context: AgentTaskContext) -> AgentResult:
    """Call the existing sanctions read boundary without any remediation action."""
    company_names = _company_names(context)
    if not company_names:
        return AgentResult(agent="compliance", status="needs_review", summary="缺少待筛查供应商名称。")

    from app.domains.risk.sanctions_service import check_sanctions

    results = [(name, await asyncio.to_thread(check_sanctions, name)) for name in company_names]
    evidence = [
        _evidence(
            name, "compliance", "制裁与黑名单筛查",
            "未命中制裁或黑名单记录。" if result.get("clean") else f"命中 {result.get('match_count', 0)} 条制裁或合规记录。",
            {"compliance": result}, confidence=0.8,
            target_fields=_target_fields(context, name),
        )
        for name, result in results
    ]
    findings = [
        {
            "type": "compliance",
            "level": _risk_level(result.get("sanctions_level")),
            "title": f"{name} 合规筛查",
            "description": f"匹配记录 {result.get('match_count', 0)} 条。",
            "confidence": 0.8,
            "evidence_ids": [f"compliance:{name}"],
        }
        for name, result in results if not result.get("clean")
    ]
    return AgentResult(
        agent="compliance", status="completed", summary=f"已完成 {len(results)} 家供应商的合规筛查。",
        evidence=evidence, findings=findings, metrics=AgentMetrics(evidence_count=len(evidence)),
    )


async def _run_sentiment(context: AgentTaskContext) -> AgentResult:
    """Use a cache first, then perform one bounded cache-only acquisition."""
    company_names = _company_names(context)
    if not company_names:
        return AgentResult(agent="sentiment", status="needs_review", summary="缺少待分析供应商名称。")

    from app.domains.risk.sentiment import _get_cached_sentiment, analyze_sentiment

    async def get_or_refresh(name: str) -> tuple[str, dict[str, Any] | None, bool]:
        cached = await asyncio.to_thread(_get_cached_sentiment, name)
        if cached is not None and not cached.get("is_stale", False):
            return name, cached, False
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    analyze_sentiment,
                    name,
                    force_refresh=cached is not None,
                    max_results=6,
                    emit_alerts=False,
                ),
                timeout=35,
            )
            return name, result, True
        except TimeoutError:
            return name, cached, True

    results = [await get_or_refresh(name) for name in company_names]
    evidence = [
        _evidence(
            name, "sentiment", "联网/天眼查舆情补采" if refreshed else "已缓存舆情分析",
            f"舆情倾向：{result.get('overall_sentiment', 'unknown')}。",
            {"sentiment": result},
            freshness="stale" if result.get("is_stale") else "fresh",
            confidence=0.65 if result.get("is_stale") else 0.8,
            target_fields=_target_fields(context, name),
        )
        for name, result, refreshed in results
        if result is not None and result.get("has_data") is not False
    ]
    findings = [
        {
            "type": "sentiment",
            "level": "high" if float(result.get("sentiment_score") or 0) <= -0.7 else "medium",
            "title": f"{name} 负面舆情",
            "description": str(result.get("summary") or "检测到负面舆情。"),
            "confidence": 0.8,
            "evidence_ids": [f"sentiment:{name}"],
        }
        for name, result, _ in results
        if result is not None and str(result.get("overall_sentiment")) == "negative"
    ]
    return AgentResult(
        agent="sentiment", status="completed" if len(evidence) == len(company_names) else "needs_review",
        summary=(
            f"已获得 {len(evidence)}/{len(company_names)} 家供应商的舆情结果；"
            f"其中 {sum(1 for _, _, refreshed in results if refreshed)} 家触发了受限补采。"
        ),
        evidence=evidence, findings=findings, metrics=AgentMetrics(evidence_count=len(evidence)),
    )


async def _run_esg(context: AgentTaskContext) -> AgentResult:
    """Calculate ESG from already cached enterprise evidence only."""
    company_names = _company_names(context)
    if not company_names:
        return AgentResult(agent="esg", status="needs_review", summary="缺少待评估供应商名称。")

    from app.domains.risk.esg_service import assess_esg

    results = [(name, await asyncio.to_thread(assess_esg, name)) for name in company_names]
    evidence = [
        _evidence(
            name, "esg", "ESG 风险指标",
            (
                f"ESG 总体等级：{result.get('total_level', '未知')}。"
                if result.get("assessment_status") == "sufficient"
                else f"ESG 数据不足（覆盖度 {result.get('data_coverage', {}).get('coverage_ratio', 0):.0%}），不能判定为低风险。"
            ),
            {"esg": result}, confidence=0.75,
            target_fields=_target_fields(context, name),
        )
        for name, result in results if result is not None
    ]
    findings = [
        {
            "type": "esg",
            "level": _risk_level(result.get("total_level")),
            "title": f"{name} ESG 风险",
            "description": (
                f"ESG 风险分 {result.get('total_score', '-')}。"
                if result.get("assessment_status") == "sufficient"
                else "关键 E/S/G 维度数据不足，结果仅供补数前参考。"
            ),
            "confidence": 0.75 if result.get("assessment_status") == "sufficient" else 0.35,
            "evidence_ids": [f"esg:{name}"],
        }
        for name, result in results if result is not None
    ]
    return AgentResult(
        agent="esg",
        status=(
            "completed"
            if len(evidence) == len(company_names)
            and all(result.get("assessment_status") == "sufficient" for _, result in results if result is not None)
            else "needs_review"
        ),
        summary=f"已完成 {len(evidence)}/{len(company_names)} 家供应商的 ESG 评估与覆盖度检查。",
        evidence=evidence, findings=findings, metrics=AgentMetrics(evidence_count=len(evidence)),
    )


AGENT_HANDLERS: dict[str, AgentHandler] = {
    "sourcing": _run_sourcing,
    "risk": _run_risk,
    "esg": _run_esg,
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
        supplier_references=[
            dict(reference)
            for reference in state.get("supplier_references", [])
            if isinstance(reference, dict)
        ],
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
