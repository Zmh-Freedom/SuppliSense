"""Sourcing schemas — 采购寻源 Pydantic 模型。"""

from datetime import datetime

from pydantic import BaseModel


class SourcingRequestInput(BaseModel):
    title: str
    category: str
    spec: str
    budget_min: float | None = None
    budget_max: float | None = None
    quantity: int | None = None
    region_required: str | None = None
    qualifications: list[str] = []


class SourcingRequestResponse(BaseModel):
    request_id: str
    status: str


class SourcingResultItem(BaseModel):
    result_id: str
    supplier_name: str
    match_score: float
    risk_score: float | None = None
    risk_level: str = "unknown"
    final_rank: float
    match_reason: str = ""
    risk_summary: str = ""
    selected: bool = False
    action: str | None = None


class SourcingSearchResponse(BaseModel):
    request_id: str
    status: str
    results: list[SourcingResultItem] = []


class SourcingRequestDetail(BaseModel):
    request_id: str
    user_id: str
    title: str
    category: str
    spec: str
    status: str
    result_count: int
    created_at: str
    completed_at: str | None = None
    results: list[SourcingResultItem] = []


class SourcingListResponse(BaseModel):
    items: list[SourcingRequestDetail] = []
    total: int = 0


class SupplierInput(BaseModel):
    name: str
    unified_code: str | None = None
    categories: list[str] = []
    regions: list[str] = []
    qualifications: list[dict] = []
    scale: dict | None = None
    contact: dict | None = None
    status: str = "prospective"


class SelectResultRequest(BaseModel):
    action: str  # watchlist | apply_access
