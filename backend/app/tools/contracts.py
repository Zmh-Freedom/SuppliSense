"""Strict output contracts for production Agent tools.

The domain services still return dictionaries for framework independence.  This
module is the boundary where those dictionaries become typed, versioned tool
results.  Nested provider payloads intentionally remain opaque mappings until
Task 13 adds field-level Claim/Evidence validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictToolOutput(BaseModel):
    """Common output metadata; unknown top-level fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    success: bool | None = None
    error: str | None = None
    message: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_records: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)


class CompanySearchOutput(StrictToolOutput):
    keyword: str
    count: int = Field(ge=0)
    results: list[dict[str, Any]] = Field(default_factory=list)


class RiskAssessmentOutput(StrictToolOutput):
    risk_score: int | float | None = None
    risk_level: str | None = None
    financial: dict[str, Any] | None = None
    risk_detail: dict[str, Any] | None = None
    score_breakdown: dict[str, Any] | None = None
    cached_at: str | None = None
    cache_age_hours: float | None = None
    is_stale: bool = False
    is_listed: bool = False


class BusinessRiskOutput(StrictToolOutput):
    assessment_status: str
    assessment_scope: str | None = None
    assessment_data_mode: str | None = None
    decision_usable: bool | None = None
    supplier_reference: str | None = None
    supplier: dict[str, Any] | None = None
    period: str | None = None
    scope: dict[str, Any] | None = None
    coverage: float | None = Field(default=None, ge=0, le=1)
    formal_business_score: float | None = None
    enabled_dimension: dict[str, Any] | None = None
    observed_signals: dict[str, Any] | None = None
    not_formally_enabled_dimensions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    reason: str | None = None
    required_data_mode: str | None = None
    available_category_codes: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class OperationalRiskOutput(StrictToolOutput):
    dimension: str
    company_name: str
    supplier_code: str | None = None
    period: str | None = None
    assessment_status: str
    risk_score: float | None = None
    risk_level: str | None = None
    data_coverage: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class AlertCheckOutput(StrictToolOutput):
    company_name: str
    monitor_target_id: str | None = None
    target_type: str | None = None
    changed: bool = False
    severity: str | None = None
    changes: list[dict[str, Any]] = Field(default_factory=list)
    previous: str | None = None
    current: str | None = None


class WatchlistOutput(StrictToolOutput):
    company_name: str | None = None
    display_name: str | None = None
    monitor_target_id: str | None = None
    target_type: str | None = None
    identity_status: str | None = None
    monitor_status: str | None = None
    added_at: datetime | str | None = None
    supplier_id: str | None = None
    candidate_id: str | None = None
    company_id: str | None = None
    supplier_code: str | None = None
    targets: list[dict[str, Any]] = Field(default_factory=list)
    companies: list[Any] = Field(default_factory=list)
    count: int | None = Field(default=None, ge=0)
    operation: str | None = None
    side_effect_receipt: dict[str, Any] | None = None


class WatchlistTrendOutput(StrictToolOutput):
    count: int = Field(default=0, ge=0)
    period_months: int | None = None
    companies: list[dict[str, Any]] = Field(default_factory=list)


class ESGOutput(StrictToolOutput):
    company_name: str
    assessed_at: str | None = None
    total_score: float | None = None
    total_level: str | None = None
    calculated_level: str | None = None
    assessment_status: str | None = None
    data_coverage: dict[str, Any] | None = None
    environmental: dict[str, Any] | None = None
    social: dict[str, Any] | None = None
    governance: dict[str, Any] | None = None


class ContagionOutput(StrictToolOutput):
    company_name: str
    related_count: int = Field(default=0, ge=0)
    branch_count: int = Field(default=0, ge=0)
    dependency_count: int = Field(default=0, ge=0)
    same_industry_count: int = Field(default=0, ge=0)
    high_risk_related_count: int = Field(default=0, ge=0)
    related_entities: list[dict[str, Any]] = Field(default_factory=list)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class SentimentOutput(StrictToolOutput):
    company_name: str
    analyzed_at: str | None = None
    sentiment_score: float | None = None
    negative_count: int = Field(default=0, ge=0)
    neutral_count: int = Field(default=0, ge=0)
    positive_count: int = Field(default=0, ge=0)
    articles_count: int = Field(default=0, ge=0)
    summary: str = ""
    key_concerns: list[Any] = Field(default_factory=list)
    risk_tags: list[Any] = Field(default_factory=list)
    articles: list[dict[str, Any]] = Field(default_factory=list)
    has_data: bool = False


class PredictionOutput(StrictToolOutput):
    company_name: str
    probability: str
    label: str
    warning_score: int = 0
    max_score: int | None = None
    signals: list[dict[str, Any]] = Field(default_factory=list)
    has_data: bool = False


class MacroRiskOutput(StrictToolOutput):
    company_name: str
    assessed_at: str | None = None
    total_score: float | None = None
    total_level: str | None = None
    policy_risks: dict[str, Any] = Field(default_factory=dict)
    regional_risk: dict[str, Any] = Field(default_factory=dict)
    industry_risk: dict[str, Any] = Field(default_factory=dict)


class AlternativesOutput(StrictToolOutput):
    company_name: str
    source_industry: str | None = None
    source_risk_score: float | None = None
    alternatives_count: int = Field(default=0, ge=0)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)


class ScenarioOutput(StrictToolOutput):
    company_name: str
    scenario: str
    scenario_desc: str | None = None
    impact_score: float | None = None
    impact_level: str | None = None
    risk_score: float | None = None
    risk_level: str | None = None
    dependent_count: int = 0
    branch_count: int = 0
    related_count: int = 0
    impact_factors: list[dict[str, Any]] = Field(default_factory=list)
    affected_parties: list[dict[str, Any]] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)


class SanctionsOutput(StrictToolOutput):
    company_name: str
    sanctions_score: float | None = None
    sanctions_level: str | None = None
    match_count: int = Field(default=0, ge=0)
    matches: list[dict[str, Any]] = Field(default_factory=list)
    clean: bool | None = None


class ComparisonOutput(StrictToolOutput):
    count: int = Field(default=0, ge=0)
    companies: list[dict[str, Any]] = Field(default_factory=list)


class TrendOutput(StrictToolOutput):
    company_name: str
    period_months: int
    trend: str
    data: list[dict[str, Any]] = Field(default_factory=list)


class FinancialOutput(StrictToolOutput):
    company_name: str
    revenue_growth: float | None = None
    net_profit_growth: float | None = None
    debt_ratio: float | None = None
    cash_flow: float | None = None
    roe: float | None = None
    net_profit_margin: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None


class ReportOutput(StrictToolOutput):
    company_name: str | None = None
    format: str | None = None
    length: int | None = None
    content: str | None = None
    size_bytes: int | None = None
    task_id: str | None = None
    cron: str | None = None
    report_type: str | None = None
    company_names: list[str] = Field(default_factory=list)
    reports: list[dict[str, Any]] = Field(default_factory=list)
    deleted: str | None = None
    cancelled: bool | None = None


class FormalSupplierOutput(StrictToolOutput):
    total: int = Field(default=0, ge=0)
    items: list[dict[str, Any]] = Field(default_factory=list)


class SourcingRequestOutput(StrictToolOutput):
    request_id: str


class SourcingSearchOutput(StrictToolOutput):
    request_id: str
    results: list[dict[str, Any]] = Field(default_factory=list)
    external_candidates: list[dict[str, Any]] = Field(default_factory=list)
    external_status: str | None = None
    external_failure_reasons: list[Any] = Field(default_factory=list)


class SourcingCandidatesOutput(StrictToolOutput):
    source: str | None = None
    source_order: list[str] = Field(default_factory=list)
    requirement: dict[str, Any] = Field(default_factory=dict)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    local_candidates: list[dict[str, Any]] = Field(default_factory=list)
    external_candidates: list[dict[str, Any]] = Field(default_factory=list)
    local_status: str | None = None
    local_failure_reason: str | None = None
    external_status: str | None = None
    external_stop_reason: str | None = None
    external_failure_reasons: list[Any] = Field(default_factory=list)
    external_loop: dict[str, Any] = Field(default_factory=dict)


class SelectionOutput(StrictToolOutput):
    action: str | None = None
    candidate_id: str | None = None
    application_id: str | None = None


class DiscoveryOutput(StrictToolOutput):
    source: str | None = None
    status: str | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    failed_stages: list[Any] = Field(default_factory=list)
    failure_reasons: list[Any] = Field(default_factory=list)


class MonitoringInvestigationOutput(StrictToolOutput):
    query: str
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    selected_candidate_id: str | None = None
    external_profile: dict[str, Any] | None = None
    data_coverage: dict[str, Any] = Field(default_factory=dict)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    evidence_summary: list[dict[str, Any]] = Field(default_factory=list)


TOOL_OUTPUT_MODELS: dict[str, type[StrictToolOutput]] = {
    "search_company": CompanySearchOutput,
    "assess_risk": RiskAssessmentOutput,
    "assess_business_risk": BusinessRiskOutput,
    "assess_operational_risk": OperationalRiskOutput,
    "check_alert": AlertCheckOutput,
    "investigate_supplier_monitoring": MonitoringInvestigationOutput,
    "get_watchlist": WatchlistOutput,
    "analyze_watchlist_trend": WatchlistTrendOutput,
    "add_to_watchlist": WatchlistOutput,
    "remove_from_watchlist": WatchlistOutput,
    "esg_assessment": ESGOutput,
    "contagion_analysis": ContagionOutput,
    "sentiment_analysis": SentimentOutput,
    "predict_risk": PredictionOutput,
    "macro_risk": MacroRiskOutput,
    "find_alternatives": AlternativesOutput,
    "scenario_simulate": ScenarioOutput,
    "check_sanctions": SanctionsOutput,
    "generate_report": ReportOutput,
    "analyze_trend": TrendOutput,
    "compare_companies": ComparisonOutput,
    "query_financials": FinancialOutput,
    "manage_scheduled_report": ReportOutput,
    "list_formal_suppliers": FormalSupplierOutput,
    "create_sourcing_request": SourcingRequestOutput,
    "search_suppliers": SourcingSearchOutput,
    "discover_supplier_candidates": SourcingCandidatesOutput,
    "select_sourcing_result": SelectionOutput,
    "expand_supplier_library": DiscoveryOutput,
    "discover_web_suppliers": DiscoveryOutput,
    "select_external_supplier_candidate": SelectionOutput,
}


__all__ = ["StrictToolOutput", "TOOL_OUTPUT_MODELS"]
