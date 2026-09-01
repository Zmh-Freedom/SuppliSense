"""Execution behavior for bounded Agent Supervisor sub-agent adapters."""

import asyncio
from types import SimpleNamespace

from app.graphs.agent_supervisor import agents as supervisor_agents
from app.graphs.agent_supervisor.agents import AGENT_HANDLERS, AgentTaskContext, run_ready_tasks
from app.graphs.agent_supervisor.contracts import AgentResult, PlannerTask, TaskPlan


def base_state() -> dict:
    return {"run_id": "run-1", "user_query": "评估供应商风险"}


def completed_result(agent: str) -> AgentResult:
    return AgentResult(agent=agent, status="completed", summary=f"{agent} completed")


def parallel_plan() -> TaskPlan:
    return TaskPlan(tasks=[
        PlannerTask(task_id="risk", agent="risk"),
        PlannerTask(task_id="compliance", agent="compliance"),
    ])


def plan_with_optional_sentiment() -> TaskPlan:
    return TaskPlan(tasks=[
        PlannerTask(task_id="risk", agent="risk"),
        PlannerTask(task_id="sentiment", agent="sentiment", required=False),
    ])


def test_ready_risk_and_compliance_tasks_run_in_parallel(monkeypatch):
    """Independent ready tasks must be scheduled together, not serially."""
    async def exercise():
        started: list[str] = []
        both_started = asyncio.Event()
        release = asyncio.Event()

        async def fake_handler(context):
            started.append(context.task.agent)
            if len(started) == 2:
                both_started.set()
            await release.wait()
            return completed_result(context.task.agent)

        monkeypatch.setitem(AGENT_HANDLERS, "risk", fake_handler)
        monkeypatch.setitem(AGENT_HANDLERS, "compliance", fake_handler)

        execution = asyncio.create_task(run_ready_tasks(parallel_plan(), base_state()))
        await asyncio.wait_for(both_started.wait(), timeout=0.2)
        release.set()
        results = await execution

        assert set(results) == {"risk", "compliance"}
        assert set(started) == {"risk", "compliance"}

    asyncio.run(exercise())


def test_one_optional_agent_failure_isolated(monkeypatch):
    """A failed optional task must not erase a completed peer result."""
    async def exercise():
        async def failing_handler(context):
            raise ValueError("provider rejected request")

        async def successful_handler(context):
            return completed_result(context.task.agent)

        monkeypatch.setitem(AGENT_HANDLERS, "sentiment", failing_handler)
        monkeypatch.setitem(AGENT_HANDLERS, "risk", successful_handler)

        results = await run_ready_tasks(plan_with_optional_sentiment(), base_state())

        assert results["sentiment"].status == "failed"
        assert results["sentiment"].error.code == "ValueError"
        assert results["risk"].status == "completed"

    asyncio.run(exercise())


def test_sourcing_worker_preserves_formal_and_external_evidence_sources(monkeypatch):
    context = AgentTaskContext(
        task=PlannerTask(task_id="sourcing", agent="sourcing"),
        run_id="run-1",
        user_query="推荐摄像头供应商",
        intent={"requirement": {"category": "摄像头"}},
        dependency_results={},
    )
    monkeypatch.setattr(
        "app.domains.sourcing_risk.discovery_service.discover_candidates",
        lambda *_: {
            "source": "local_and_external",
            "local_candidates": [{
                "supplier_id": "formal-1",
                "supplier_name": "正式供应商",
                "source_stage": "feishu_formal",
            }],
            "external_candidates": [{
                "candidate_id": "candidate-1",
                "supplier_name": "外部候选",
                "candidate_type": "external",
                "source_stage": "external",
            }],
        },
    )

    result = asyncio.run(supervisor_agents._run_sourcing(context))

    assert result.status == "completed"
    assert [item.source for item in result.evidence] == [
        "飞书正式供应商快照",
        "外部联网/天眼查待核验候选",
    ]
    assert result.evidence[1].evidence_id == "supplier:candidate-1"
    assert "外部待核验候选" in result.summary


def test_retryable_timeout_is_retried_once_before_failure(monkeypatch):
    """A retryable provider timeout gets exactly one bounded retry."""
    async def exercise():
        attempts = 0

        async def timeout_handler(context):
            nonlocal attempts
            attempts += 1
            raise TimeoutError("provider timed out")

        monkeypatch.setitem(AGENT_HANDLERS, "risk", timeout_handler)

        results = await run_ready_tasks(
            TaskPlan(tasks=[PlannerTask(task_id="risk", agent="risk")]), base_state()
        )

        assert attempts == 2
        assert results["risk"].status == "failed"
        assert results["risk"].error.retryable is True
        assert results["risk"].metrics.attempts == 2

    asyncio.run(exercise())


def test_four_risk_workers_keep_all_structured_supplier_targets(monkeypatch):
    """A multi-supplier request must not silently collapse to the first name."""
    targets = ["供应商甲", "供应商乙"]
    context = AgentTaskContext(
        task=PlannerTask(task_id="risk", agent="risk"),
        run_id="run-1",
        user_query="对这两家做风险、ESG、舆情和合规分析",
        intent={"target_supplier_names": targets},
        dependency_results={},
    )

    def risk_preview(name):
        return SimpleNamespace(
            risk_score=18,
            risk_level="低风险",
            risk_detail={"data_coverage": {"assessment_status": "complete"}},
            model_dump=lambda: {
                "company_name": name,
                "risk_score": 18,
                "risk_level": "低风险",
                "risk_detail": {"data_coverage": {"assessment_status": "complete"}},
            },
        )

    monkeypatch.setattr(
        "app.domains.risk.service.calculate_company_risk_preview",
        risk_preview,
    )
    monkeypatch.setattr(
        "app.domains.risk.sanctions_service.check_sanctions",
        lambda name: {"company_name": name, "clean": True, "match_count": 0, "sanctions_level": "low"},
    )
    monkeypatch.setattr(
        "app.domains.risk.sentiment._get_cached_sentiment",
        lambda name: {
            "company_name": name,
            "overall_sentiment": "neutral",
            "is_stale": False,
            "has_data": True,
        },
    )
    monkeypatch.setattr(
        "app.domains.risk.esg_service.assess_esg",
        lambda name: {
            "company_name": name,
            "total_level": "低风险",
            "total_score": 5,
            "assessment_status": "sufficient",
        },
    )

    async def exercise():
        results = await asyncio.gather(
            supervisor_agents._run_risk(context),
            supervisor_agents._run_esg(context),
            supervisor_agents._run_compliance(context),
            supervisor_agents._run_sentiment(context),
        )
        assert all(result.status == "completed" for result in results)
        for result in results:
            assert {item.company_id for item in result.evidence} == set(targets)

    asyncio.run(exercise())


def test_risk_worker_uses_read_only_v2_score_and_marks_partial_coverage(monkeypatch):
    context = AgentTaskContext(
        task=PlannerTask(task_id="risk", agent="risk"),
        run_id="run-1",
        user_query="评估供应商风险",
        intent={"target_supplier_names": ["供应商甲"]},
        dependency_results={},
    )
    preview = SimpleNamespace(
        risk_score=12,
        risk_level="低风险",
        risk_detail={"data_coverage": {"assessment_status": "partial", "coverage_ratio": 0.25}},
        model_dump=lambda: {
            "risk_score": 12,
            "risk_level": "低风险",
            "risk_detail": {"data_coverage": {"assessment_status": "partial", "coverage_ratio": 0.25}},
        },
    )
    monkeypatch.setattr(
        "app.domains.risk.service.calculate_company_risk_preview",
        lambda name: preview,
    )

    result = asyncio.run(supervisor_agents._run_risk(context))

    assert result.status == "needs_review"
    assert "评分：12 / 100" in result.evidence[0].claim
    assert "不足以形成综合风险结论" in result.evidence[0].claim
    assert result.findings[0].level == "unknown"
    assert result.evidence[0].metadata["risk"]["risk_detail"]["data_coverage"]["assessment_status"] == "partial"


def test_esg_worker_never_labels_insufficient_data_as_low_risk(monkeypatch):
    context = AgentTaskContext(
        task=PlannerTask(task_id="esg", agent="esg"),
        run_id="run-1",
        user_query="做 ESG 分析",
        intent={"target_supplier_names": ["供应商甲"]},
        dependency_results={},
    )
    monkeypatch.setattr(
        "app.domains.risk.esg_service.assess_esg",
        lambda name: {
            "company_name": name,
            "total_score": 5,
            "total_level": "数据不足",
            "assessment_status": "insufficient_data",
            "data_coverage": {"coverage_ratio": 0.25},
        },
    )

    result = asyncio.run(supervisor_agents._run_esg(context))

    assert result.status == "needs_review"
    assert "不能判定为低风险" in result.evidence[0].claim
    assert result.findings[0].level == "unknown"


def test_sentiment_worker_refreshes_missing_cache_without_alert_side_effect(monkeypatch):
    context = AgentTaskContext(
        task=PlannerTask(task_id="sentiment", agent="sentiment"),
        run_id="run-1",
        user_query="做舆情分析",
        intent={"target_supplier_names": ["供应商甲"]},
        dependency_results={},
    )
    calls: list[dict] = []
    monkeypatch.setattr("app.domains.risk.sentiment._get_cached_sentiment", lambda name: None)

    def analyze(name, **kwargs):
        calls.append(kwargs)
        return {
            "company_name": name,
            "has_data": True,
            "overall_sentiment": "neutral",
            "is_stale": False,
            "articles_count": 1,
        }

    monkeypatch.setattr("app.domains.risk.sentiment.analyze_sentiment", analyze)

    result = asyncio.run(supervisor_agents._run_sentiment(context))

    assert result.status == "completed"
    assert result.evidence[0].source == "联网/天眼查舆情补采"
    assert calls == [{"force_refresh": False, "max_results": 6, "emit_alerts": False}]
