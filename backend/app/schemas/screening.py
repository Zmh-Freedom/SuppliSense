"""Versioned read contracts for screening and supplier compliance."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ScreeningReadResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1] = 1
    company_name: str
    entity_id: str | None = None
    scope: str | None = None
    screening_status: str
    coverage: dict[str, Any]
    matches: list[dict[str, Any]]
    domestic_findings: list[dict[str, Any]]
    limitations: list[str]
    checked_at: str


class SupplierComplianceReadResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1] = 1
    screening: ScreeningReadResponse
    domestic_findings: list[dict[str, Any]]
    limitations: list[str]
