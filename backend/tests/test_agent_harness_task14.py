"""Task 14 regressions for the sourcing workflow on the active Harness path."""

from __future__ import annotations

import asyncio

import pytest

from app.domains.sourcing_risk import discovery_service
from app.domains.sourcing_risk.requirement_service import resolve_harness_requirement
from app.graphs.agent_core.adapter import _apply_harness_sourcing_requirement
from app.graphs.agent_core.adapter import _enforce_scope_query_intent
from app.graphs.agent_core.answer_contract import AgentAnswer
from app.graphs.agent_core.evidence_ledger import ValidatedClaim
from app.graphs.harness.graph import _build_default_plan, _summary
from app.domains.risk import tools_risk
from app.tools import TOOL_REGISTRY
from app.tools.executor import ToolContext, ToolExecutor


pytestmark = pytest.mark.agent_e2e


def test_harness_requirement_extracts_broad_category_without_inventing_constraints(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing_risk.requirement_service.settings.LLM_API_KEY", "")

    result = resolve_harness_requirement("给我推荐工业相机的供应商")

    assert result["status"] == "ready"
    assert result["extraction_source"] == "deterministic_fallback"
    assert result["requirement"]["category"] == "工业相机"
    assert result["requirement"]["product"] == "工业相机"
    assert result["requirement"]["must_have"] == []


def test_harness_requirement_extracts_complete_natural_language_purchase_request(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing_risk.requirement_service.settings.LLM_API_KEY", "")

    result = resolve_harness_requirement(
        "采购工业摄像头，要求 IP67，支持 PoE，优先华东地区交付"
    )

    assert result["status"] == "ready"
    assert result["extraction_source"] == "deterministic_fallback"
    assert result["requirement"]["category"] == "工业摄像头"
    assert result["requirement"]["specification"] == "IP67，支持 PoE"
    assert result["requirement"]["region"] == "华东"


def test_harness_requirement_extracts_material_number_for_read_only_sourcing(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing_risk.requirement_service.settings.LLM_API_KEY", "")

    result = resolve_harness_requirement("帮我为物料号 23748163 寻找供应商")

    assert result["status"] == "ready"
    assert result["requirement"]["material"] == "23748163"
    assert result["requirement"]["specification"] == "23748163"


def test_harness_requirement_extracts_history_material_name(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing_risk.requirement_service.settings.LLM_API_KEY", "")

    result = resolve_harness_requirement("后轮制动鼓有哪些历史合作供应商？再补充盖世候选")

    assert result["status"] == "ready"
    assert result["requirement"]["material"] == "后轮制动鼓"


def test_harness_binds_requirement_to_current_task_before_planning(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing_risk.requirement_service.settings.LLM_API_KEY", "")
    context = {
        "references": [],
        "conversation_state": {},
        "current_task": {"task_id": "source-1", "task_type": "sourcing"},
    }

    resolved = _apply_harness_sourcing_requirement(context, "推荐钢材供应商")

    task = resolved["current_task"]
    assert task["requirement_status"] == "ready"
    assert task["requirement"]["category"] == "钢材"
    planned = _build_default_plan({"current_task": task, "execution_context": resolved})
    assert planned[0].tool_name == "discover_supplier_candidates"
    assert planned[0].arguments["requirement"]["category"] == "钢材"


def test_harness_new_sourcing_turn_replaces_previous_category(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing_risk.requirement_service.settings.LLM_API_KEY", "")
    context = {
        "references": [],
        "conversation_state": {
            "current_requirement": {
                "category": "蓄电池",
                "product": "蓄电池",
                "specification": "蓄电池",
            },
        },
        "current_task": {
            "task_id": "source-2",
            "task_type": "sourcing",
        },
    }

    resolved = _apply_harness_sourcing_requirement(
        context,
        "找一下做安全带的供应商",
    )

    assert resolved["current_task"]["requirement"]["category"] == "安全带"
    assert resolved["conversation_state"]["current_requirement"]["category"] == "安全带"


def test_sourcing_summary_explains_no_match_category() -> None:
    summary = _summary(
        AgentAnswer(status="completed", summary="已完成寻源"),
        {
            "current_task": {"analysis_dimensions": ["sourcing"]},
            "task_specs": [{"tool_name": "discover_supplier_candidates"}],
            "tool_outcomes": [{
                "tool_name": "discover_supplier_candidates",
                "data": {
                    "status": "not_found",
                    "requirement": {"category": "安全带"},
                    "local_candidates": [],
                    "external_candidates": [],
                    "external_status": "not_found",
                },
            }],
        },
    )

    assert "按“安全带”完成历史合作和外部候选检索" in summary
    assert "当前没有匹配候选" in summary
    assert "没有返回可用候选" not in summary


def test_sourcing_summary_explains_external_failure() -> None:
    summary = _summary(
        AgentAnswer(status="partial", summary="已完成寻源"),
        {
            "current_task": {"analysis_dimensions": ["sourcing"]},
            "task_specs": [{"tool_name": "discover_supplier_candidates"}],
            "tool_outcomes": [{
                "tool_name": "discover_supplier_candidates",
                "data": {
                    "status": "partial",
                    "requirement": {"category": "安全带"},
                    "local_candidates": [],
                    "external_candidates": [],
                    "external_status": "failed",
                    "external_failure_reasons": [{"stage": "web_search"}],
                },
            }],
        },
    )

    assert "外部候选来源暂时不可用" in summary
    assert "稍后重试" in summary


def test_harness_does_not_recommend_unfiltered_suppliers_without_category() -> None:
    planned = _build_default_plan({
        "current_task": {
            "task_id": "source-unknown",
            "task_type": "sourcing",
            "user_message": "帮我推荐供应商",
        },
        "execution_context": {"references": []},
    })

    assert planned == []


def test_harness_recognizes_formal_supplier_directory_query() -> None:
    planned = _build_default_plan({
        "current_task": {
            "task_id": "formal-directory",
            "task_type": "sourcing",
            "user_message": "查询当前正式供应商",
        },
        "execution_context": {"references": []},
    })

    assert len(planned) == 1
    assert planned[0].tool_name == "list_formal_suppliers"


def test_scope_query_cannot_become_single_supplier_analysis_from_llm_noise() -> None:
    context = {
        "current_task": {
            "task_type": "analysis",
            "target_supplier_names": ["模型误识别的公司"],
            "analysis_dimensions": ["risk"],
            "user_message": "查看监控清单",
        },
        "conversation_state": {},
        "llm_intent": {
            "target_supplier_names": ["模型误识别的公司"],
            "analysis_dimensions": ["risk"],
            "task_type": "analysis",
            "requested_action": "none",
        },
    }

    resolved = _enforce_scope_query_intent(context, "查看监控清单")

    assert resolved["current_task"]["target_supplier_names"] == []
    assert resolved["current_task"]["analysis_dimensions"] == []
    assert resolved["llm_intent"]["target_supplier_names"] == []


def test_explicit_watchlist_write_is_not_downgraded_to_scope_query() -> None:
    context = {
        "current_task": {
            "task_id": "watch-write",
            "task_type": "analysis",
            "target_supplier_names": ["上海海拉电子有限公司"],
            "analysis_dimensions": [],
            "user_message": "把上海海拉电子有限公司加入到监控清单中",
        },
        "conversation_state": {},
        "llm_intent": {
            "target_supplier_names": ["上海海拉电子有限公司"],
            "analysis_dimensions": [],
            "task_type": "analysis",
            "requested_action": "add_watchlist",
        },
    }

    resolved = _enforce_scope_query_intent(
        context, "把上海海拉电子有限公司加入到监控清单中"
    )

    assert resolved["current_task"]["target_supplier_names"] == ["上海海拉电子有限公司"]
    assert resolved["llm_intent"]["requested_action"] == "add_watchlist"
    planned = _build_default_plan({
        "current_task": resolved["current_task"],
        "execution_context": {"references": []},
    })
    assert planned[0].tool_name == "investigate_supplier_monitoring"


def test_explicit_watchlist_write_has_deterministic_fallback_without_llm() -> None:
    from app.graphs.agent_core.adapter import build_execution_context

    context = build_execution_context(
        session_id="watch-write-fallback",
        user_message="把上海海拉电子有限公司加入到监控清单中",
    )

    resolved = _enforce_scope_query_intent(
        context, "把上海海拉电子有限公司加入到监控清单中"
    )

    assert resolved["current_task"]["target_supplier_names"] == ["上海海拉电子有限公司"]
    assert resolved["llm_intent"]["requested_action"] == "add_watchlist"


def test_supplier_review_summary_answers_the_business_question_first() -> None:
    answer = AgentAnswer(
        status="completed",
        summary="待生成",
        claims=[
            ValidatedClaim(
                claim_id="risk", entity_id="entity:supplier", dimension="risk",
                statement="青岛三祥科技股份有限公司 综合风险评分：7/100",
                value=7, fact_path="risk_score", evidence_refs=["risk"], confidence=0.9,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="profit", entity_id="entity:supplier", dimension="financial",
                statement="青岛三祥科技股份有限公司 净利润同比增长率：-18.1%",
                value=-0.181, fact_path="net_profit_growth", evidence_refs=["financial"], confidence=0.9,
                validation_status="supported",
            ),
        ],
    )

    summary = _summary(answer, {
        "current_task": {
            "target_supplier_names": ["青岛三祥科技股份有限公司"],
            "analysis_dimensions": ["risk", "financial"],
        },
        "task_specs": [],
        "tool_outcomes": [],
    })

    assert "青岛三祥科技股份有限公司" in summary
    assert "综合安全评分 7/100" in summary
    assert "净利润同比下降 18.1%" in summary
    assert "证据复核点" not in summary


def test_prediction_summary_surfaces_prediction_before_generic_review() -> None:
    answer = AgentAnswer(
        status="completed",
        summary="待生成",
        claims=[
            ValidatedClaim(
                claim_id="prediction-probability", entity_id="entity:supplier", dimension="risk_prediction",
                statement="青岛三祥科技股份有限公司 未来 6-12 个月风险恶化概率：可能恶化",
                value="medium", fact_path="probability", evidence_refs=["prediction"], confidence=0.85,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="prediction-label", entity_id="entity:supplier", dimension="risk_prediction",
                statement="青岛三祥科技股份有限公司 风险预测结论：可能恶化",
                value="可能恶化", fact_path="label", evidence_refs=["prediction"], confidence=0.85,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="prediction-score", entity_id="entity:supplier", dimension="risk_prediction",
                statement="青岛三祥科技股份有限公司 风险预警分数：4",
                value=4, fact_path="warning_score", evidence_refs=["prediction"], confidence=0.85,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="prediction-max", entity_id="entity:supplier", dimension="risk_prediction",
                statement="青岛三祥科技股份有限公司 风险预测满分：14",
                value=14, fact_path="max_score", evidence_refs=["prediction"], confidence=0.85,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="prediction-data", entity_id="entity:supplier", dimension="risk_prediction",
                statement="青岛三祥科技股份有限公司 预测数据可用：是",
                value=True, fact_path="has_data", evidence_refs=["prediction"], confidence=0.85,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="prediction-signals", entity_id="entity:supplier", dimension="risk_prediction",
                statement="青岛三祥科技股份有限公司 风险预测信号：营收轻微下滑",
                value="营收轻微下滑", fact_path="prediction_signal_summary", evidence_refs=["prediction"], confidence=0.85,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="risk", entity_id="entity:supplier", dimension="risk",
                statement="青岛三祥科技股份有限公司 综合风险评分：93/100",
                value=93, fact_path="risk_score", evidence_refs=["risk"], confidence=0.9,
                validation_status="supported",
            ),
        ],
    )

    summary = _summary(answer, {
        "current_task": {
            "target_supplier_names": ["青岛三祥科技股份有限公司"],
            "analysis_dimensions": ["risk"],
        },
        "task_specs": [{"tool_name": "predict_risk"}],
        "tool_outcomes": [],
    })

    assert "未来 6-12 个月风险趋势预测" in summary
    assert "风险恶化判断：可能恶化" in summary
    assert "营收轻微下滑" in summary
    assert "供应商复核" not in summary


@pytest.mark.parametrize(
    ("message", "tool_name", "dimension", "fact_path", "value", "expected"),
    [
        ("查看测试供应商的财务数据", "query_financials", "financial", "net_profit_growth", -0.1, "财务分析"),
        ("分析测试供应商的舆情", "sentiment_analysis", "sentiment", "overall_sentiment", "neutral", "舆情分析"),
        ("对测试供应商进行合规筛查", "check_sanctions", "compliance", "clean", True, "合规与制裁筛查"),
        ("评估测试供应商的 ESG 风险", "esg_assessment", "esg", "total_score", 20, "ESG 评估"),
        ("查看测试供应商最近 6 个月的风险趋势", "analyze_trend", "risk_trend", "trend", "稳定", "历史风险趋势分析"),
        ("查询测试供应商的司法风险", "lookup_legal_risk", "legal_risk", None, 2, "司法风险检索"),
        ("生成测试供应商的风险评估报告", "generate_report", "report", "format", "html", "风险报告生成"),
    ],
)
def test_summary_routes_explicit_capability_before_generic_review(
    message: str,
    tool_name: str,
    dimension: str,
    fact_path: str | None,
    value: object,
    expected: str,
) -> None:
    answer = AgentAnswer(
        status="completed",
        summary="待生成",
        claims=[ValidatedClaim(
            claim_id=f"{tool_name}-claim",
            entity_id="entity:supplier",
            dimension=dimension,
            statement=f"测试供应商 {fact_path or '司法检查'}：{value}",
            value=value,
            fact_path=fact_path,
            evidence_refs=[tool_name],
            confidence=0.85,
            validation_status="supported",
        )],
    )

    summary = _summary(answer, {
        "current_task": {
            "user_message": message,
            "target_supplier_names": ["测试供应商有限公司"],
            "analysis_dimensions": [dimension],
        },
        "task_specs": [{"tool_name": tool_name}],
        "tool_outcomes": [],
    })

    assert expected in summary
    assert "供应商复核" not in summary


def test_predict_risk_binds_prediction_fields_to_claims(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.domains.risk.predictor.predict_company",
        lambda _name: {
            "company_name": "测试供应商有限公司",
            "probability": "medium",
            "label": "可能恶化",
            "warning_score": 4,
            "max_score": 14,
            "prediction_signal_summary": "营收轻微下滑",
            "has_data": True,
        },
    )

    result = tools_risk.predict_risk.invoke({"company_name": "测试供应商有限公司"})

    assert {claim["fact_path"] for claim in result["claims"]} == {
        "probability", "label", "warning_score", "max_score", "prediction_signal_summary", "has_data",
    }


def test_contagion_summary_answers_network_question_before_generic_review() -> None:
    answer = AgentAnswer(
        status="completed",
        summary="待生成",
        claims=[
            ValidatedClaim(
                claim_id="network-related", entity_id="entity:supplier", dimension="risk_network",
                statement="青岛三祥科技股份有限公司 关联主体数量：3",
                value=3, fact_path="related_count", evidence_refs=["network"], confidence=0.85,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="network-dependency", entity_id="entity:supplier", dimension="risk_network",
                statement="青岛三祥科技股份有限公司 供应链依赖数量：2",
                value=2, fact_path="dependency_count", evidence_refs=["network"], confidence=0.85,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="network-high-risk", entity_id="entity:supplier", dimension="risk_network",
                statement="青岛三祥科技股份有限公司 高风险关联主体数量：1",
                value=1, fact_path="high_risk_related_count", evidence_refs=["network"], confidence=0.85,
                validation_status="supported",
            ),
            ValidatedClaim(
                claim_id="risk", entity_id="entity:supplier", dimension="risk",
                statement="青岛三祥科技股份有限公司 综合风险评分：93/100",
                value=93, fact_path="risk_score", evidence_refs=["risk"], confidence=0.9,
                validation_status="supported",
            ),
        ],
    )

    summary = _summary(answer, {
        "current_task": {
            "target_supplier_names": ["青岛三祥科技股份有限公司"],
            "analysis_dimensions": ["risk"],
        },
        "task_specs": [{"tool_name": "contagion_analysis"}],
        "tool_outcomes": [],
    })

    assert "供应链关系与传染风险分析" in summary
    assert "关联主体 3 家" in summary
    assert "供应链依赖 2 条" in summary
    assert "高风险关联主体 1 家" in summary
    assert "供应商复核" not in summary


def test_contagion_analysis_binds_network_fields_to_claims(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.domains.risk.contagion.analyze_contagion",
        lambda _name: {
            "company_name": "测试供应商有限公司",
            "related_count": 3,
            "branch_count": 1,
            "dependency_count": 2,
            "same_industry_count": 0,
            "high_risk_related_count": 1,
            "related_entities": [],
        },
    )

    from app.domains.risk import tools_analysis

    result = tools_analysis.contagion_analysis.invoke({"company_name": "测试供应商有限公司"})

    assert {claim["fact_path"] for claim in result["claims"]} == {
        "related_count", "branch_count", "dependency_count", "same_industry_count", "high_risk_related_count",
    }


@pytest.mark.parametrize(
    ("message", "tool_name"),
    [
        ("查看监控清单", "get_watchlist"),
        ("分析我负责的供应商本月风险变化", "analyze_watchlist_trend"),
        ("查询本人负责供应商本月风险变化", "analyze_watchlist_trend"),
        ("查看我科室所有供应商的待复核事项", "get_monitor_review_queue"),
    ],
)
def test_harness_plans_scope_level_procurement_queries(message: str, tool_name: str) -> None:
    planned = _build_default_plan({
        "current_task": {
            "task_id": "scope-query",
            "task_type": "sourcing",
            "user_message": message,
            "target_supplier_names": [],
            "analysis_dimensions": [],
        },
        "execution_context": {"references": []},
    })

    assert len(planned) == 1
    assert planned[0].tool_name == tool_name


def test_harness_builds_discovery_plan_for_risk_filtered_sourcing() -> None:
    planned = _build_default_plan({
        "current_task": {
            "task_id": "risk-filtered-sourcing",
            "task_type": "sourcing",
            "user_message": "帮我找光电器件领域风险最低的供应商",
            "requirement": {
                "category": "光电器件",
                "specification": "光电器件",
            },
        },
        "execution_context": {"references": []},
    })

    assert len(planned) == 1
    assert planned[0].tool_name == "discover_supplier_candidates"


@pytest.mark.parametrize(
    ("message", "tool_name"),
    [
        ("分析青岛三祥科技股份有限公司的供应链关系和传染风险", "contagion_analysis"),
        ("预测青岛三祥科技股份有限公司未来6-12个月的风险趋势", "predict_risk"),
        ("分析青岛三祥科技股份有限公司的舆情", "sentiment_analysis"),
        ("生成青岛三祥科技股份有限公司的风险评估报告", "generate_report"),
    ],
)
def test_harness_maps_explicit_capabilities_to_tools(message: str, tool_name: str) -> None:
    planned = _build_default_plan({
        "current_task": {
            "task_id": "capability-query",
            "task_type": "analysis",
            "user_message": message,
            "target_supplier_names": ["青岛三祥科技股份有限公司"],
            "analysis_dimensions": ["risk"],
        },
        "execution_context": {"references": []},
    })

    assert any(task.tool_name == tool_name and task.required for task in planned)


def test_harness_sourcing_keeps_formal_candidates_when_external_stage_fails(monkeypatch) -> None:
    formal = {
        "supplier_id": "supplier-1",
        "supplier_name": "华东钢材有限公司",
        "categories": ["钢材"],
        "source": "feishu_bitable",
        "source_type": "feishu_bitable",
        "source_stage": "feishu_formal",
        "source_reference": "supplier-record-1",
        "website_url": "https://steel.example.test",
    }
    monkeypatch.setattr(
        discovery_service,
        "discover_candidates",
        lambda *_args: {
            "source": "local",
            "source_order": ["local_history", "feishu_formal", "tianyancha", "web_search"],
            "local_candidates": [formal],
            "local_status": "ok",
            "local_failure_reason": None,
            "external_candidates": [],
            "external_status": "failed",
            "external_stop_reason": "external_sources_failed",
            "external_failure_reasons": [{"stage": "tianyancha", "reason": "阶段调用失败"}],
            "external_loop": {"iterations": 1, "max_iterations": 3},
        },
    )

    outcome = asyncio.run(
        ToolExecutor(TOOL_REGISTRY).execute(
            "discover_supplier_candidates",
            {"requirement": {"category": "钢材", "specification": "钢材"}},
            ToolContext(session_id="s", run_id="r"),
        )
    )

    assert outcome.status == "success"
    assert outcome.data["candidates"][0]["candidate_type"] == "formal"
    assert outcome.data["candidates"][0]["identity_status"] == "exact"
    assert outcome.data["external_status"] == "failed"
    assert outcome.data["external_failure_reasons"]
    assert outcome.data["claims"]
    assert outcome.data["evidence_records"]


def test_harness_sourcing_exposes_external_candidate_as_pending_verification(monkeypatch) -> None:
    external = {
        "candidate_id": "candidate-1",
        "supplier_name": "公开钢材候选有限公司",
        "source": "web_search",
        "source_type": "public_web_search",
        "source_stage": "external",
        "source_reference": "https://example.test/supplier",
        "identity_status": "unavailable",
    }
    monkeypatch.setattr(
        discovery_service,
        "discover_candidates",
        lambda *_args: {
            "source": "local_and_external",
            "source_order": ["local_history", "feishu_formal", "tianyancha", "web_search"],
            "local_candidates": [],
            "local_status": "ok",
            "external_candidates": [external],
            "external_status": "partial",
            "external_stop_reason": "external_sources_exhausted",
            "external_failure_reasons": [],
            "external_loop": {"iterations": 3, "max_iterations": 3},
        },
    )

    outcome = asyncio.run(
        ToolExecutor(TOOL_REGISTRY).execute(
            "discover_supplier_candidates",
            {"requirement": {"category": "钢材"}},
            ToolContext(session_id="s", run_id="r"),
        )
    )

    candidate = outcome.data["external_candidates"][0]
    assert outcome.status == "success"
    assert candidate["candidate_type"] == "external"
    assert candidate["identity_status"] == "unavailable"
    assert candidate["verification_status"] == "pending_verification"
    assert outcome.data["source_order"][-2:] == ["tianyancha", "web_search"]
