"""Shared risk capability contracts used by the Agent Harness.

This module contains policy metadata only.  It deliberately does not import
LangGraph or call a provider, so the domain contract can also be used by
offline tests and future API adapters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RiskDimension = Literal[
    "risk", "financial", "business_risk", "quality", "delivery",
    "compliance", "esg", "sentiment",
]


class RiskAssessmentRequest(BaseModel):
    """Validated internal request for one or more supplier risk dimensions."""

    model_config = ConfigDict(extra="forbid")

    suppliers: list[str] = Field(min_length=1, max_length=10)
    dimensions: list[RiskDimension] = Field(min_length=1, max_length=8)
    period: Literal["latest", "quarterly", "annual"] = "quarterly"
    include_trend: bool = False

    @model_validator(mode="after")
    def normalize_values(self) -> "RiskAssessmentRequest":
        self.suppliers = list(dict.fromkeys(item.strip() for item in self.suppliers if item.strip()))
        self.dimensions = list(dict.fromkeys(self.dimensions))
        if not self.suppliers:
            raise ValueError("至少需要一个供应商")
        if not self.dimensions:
            raise ValueError("至少需要一个风险维度")
        return self


class RiskDimensionSpec(BaseModel):
    """Execution and evidence expectations for one risk dimension."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: RiskDimension
    tool_name: str
    argument_name: Literal["company_name", "supplier_reference"]
    evidence_requirements: tuple[str, ...]
    time_window: str
    supports_synthetic: bool = False
    missing_data_terminal: str = "missing_data"


RISK_DIMENSION_SPECS: dict[str, RiskDimensionSpec] = {
    "risk": RiskDimensionSpec(
        dimension="risk", tool_name="assess_risk", argument_name="company_name",
        evidence_requirements=("risk",), time_window="latest_available",
    ),
    "financial": RiskDimensionSpec(
        dimension="financial", tool_name="query_financials", argument_name="company_name",
        evidence_requirements=("financial",), time_window="quarterly_with_annual_trend",
    ),
    "business_risk": RiskDimensionSpec(
        dimension="business_risk", tool_name="assess_business_risk", argument_name="supplier_reference",
        evidence_requirements=("business_risk",), time_window="monthly_snapshot",
    ),
    "quality": RiskDimensionSpec(
        dimension="quality", tool_name="assess_operational_risk", argument_name="company_name",
        evidence_requirements=("quality",), time_window="latest_monthly_snapshot",
    ),
    "delivery": RiskDimensionSpec(
        dimension="delivery", tool_name="assess_operational_risk", argument_name="company_name",
        evidence_requirements=("delivery",), time_window="latest_monthly_snapshot",
    ),
    "compliance": RiskDimensionSpec(
        dimension="compliance", tool_name="check_sanctions", argument_name="company_name",
        evidence_requirements=("compliance",), time_window="latest_available",
    ),
    "esg": RiskDimensionSpec(
        dimension="esg", tool_name="esg_assessment", argument_name="company_name",
        evidence_requirements=("esg",), time_window="latest_available",
    ),
    "sentiment": RiskDimensionSpec(
        dimension="sentiment", tool_name="sentiment_analysis", argument_name="company_name",
        evidence_requirements=("sentiment",), time_window="latest_available",
    ),
}


def get_risk_dimension_spec(dimension: str) -> RiskDimensionSpec | None:
    """Return a stable capability spec for a normalized dimension."""
    return RISK_DIMENSION_SPECS.get(dimension)


__all__ = [
    "RISK_DIMENSION_SPECS", "RiskAssessmentRequest", "RiskDimension",
    "RiskDimensionSpec", "get_risk_dimension_spec",
]


OperationalDimension = Literal['quality', 'delivery']


class OperationalSourceContract(BaseModel):
    """Trusted source configuration, never inferred from a business row."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    source: str
    version: str
    dimension: OperationalDimension
    rate_fields: tuple[str, ...]
    rate_unit: Literal['ratio', 'percent']
    rate_kind: Literal['pass', 'on_time', 'reject', 'late', 'defect_occurrence']
    numerator_field: str | None = None
    denominator_field: str | None = None
    denominator_basis: Literal['inspected_units', 'ordered_units', 'order_lines', 'batches'] | None = None
    complement_allowed: bool = False
    quantity_unit_field: str | None = None
    scope_fields: tuple[str, ...]
    record_id_field: str
    partition_field: str | None = None
    row_kind: Literal['detail', 'summary']
    observed_at_field: str
    valid_until_field: str


class OperationalObservation(BaseModel):
    """One source observation; numeric validity is distinct from freshness."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    metric: str
    raw_value: float | None = None
    raw_unit: str
    value: float | None = None
    numerator: float | None = None
    denominator: float | None = None
    denominator_basis: str | None = None
    quantity_unit: str | None = None
    period: str
    scope: dict[str, str]
    source: str
    source_record_id: str
    partition: str | None = None
    row_kind: str
    source_version: str
    observed_at: 'datetime | None' = None
    valid_until: 'datetime | None' = None
    status: Literal['available', 'unavailable', 'conflicting']
    reasons: tuple[str, ...] = ()


class OperationalMetricResult(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    metric: str
    period: str | None = None
    value: float | None = None
    numerator: float | None = None
    denominator: float | None = None
    observations: tuple[OperationalObservation, ...] = ()
    status: Literal['available', 'partial', 'unavailable', 'conflicting']
    reasons: tuple[str, ...] = ()
