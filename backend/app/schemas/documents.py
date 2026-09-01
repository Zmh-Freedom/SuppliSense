"""MongoDB 文档 Pydantic 校验模型。

Repository 层写入前使用这些模型校验，防止脏数据入库。
"""

from datetime import datetime

from pydantic import BaseModel, Field


# ---- Supplier ----

class SupplierDocument(BaseModel):
    """suppliers 集合文档。"""

    name: str
    unified_code: str | None = None
    legal_person: str | None = None
    registered_capital: str | None = None
    establish_time: str | None = None
    reg_status: str | None = None
    industry: str | None = None
    categories: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    # 联系方式
    contact_person: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    website_url: str | None = None
    address: str | None = None
    # 经营信息
    scale: str | None = None
    description: str | None = None
    certifications: list[dict] = Field(default_factory=list)
    annual_revenue: float | None = None
    credit_rating: str | None = None
    # 状态
    status: str = "prospective"
    source: str = "manual"
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---- Sourcing Request ----

class SourcingRequestDocument(BaseModel):
    """sourcing_requests 集合文档。"""
    user_id: str
    title: str
    category: str
    spec: str = ""
    budget_min: float | None = None
    budget_max: float | None = None
    quantity: int | None = None
    region_required: str | None = None
    qualifications: list[str] = Field(default_factory=list)
    status: str = "draft"
    result_count: int = 0
    created_at: datetime | None = None
    completed_at: datetime | None = None


# ---- Sourcing Result ----

class SourcingResultDocument(BaseModel):
    """sourcing_results 集合文档。"""
    request_id: str
    supplier_name: str
    supplier_id: str | None = None
    supplier_code: str | None = None
    match_score: float
    risk_score: float | None = None
    risk_level: str = "unknown"
    final_rank: float
    match_reason: str = ""
    risk_summary: str = ""
    industry: str | None = None
    categories: list[str] = Field(default_factory=list)
    capabilities: list[dict] = Field(default_factory=list)
    contacts: list[dict] = Field(default_factory=list)
    website_url: str | None = None
    contact_person: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    source: str | None = None
    source_updated_at: datetime | str | None = None
    selected: bool = False
    action: str | None = None
    created_at: datetime | None = None


# ---- Access Application ----

class AccessApplicationDocument(BaseModel):
    """access_applications 集合文档。"""
    supplier_name: str
    supplier_id: str | None = None
    candidate_id: str | None = None
    request_id: str | None = None
    applicant_id: str
    status: str = "pending"
    reviewer_id: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime | None = None


# ---- Supplier Changelog ----

class SupplierChangelogDocument(BaseModel):
    """supplier_changelog 集合文档。"""
    supplier_id: str
    supplier_name: str
    changed: dict  # {field: {old: ..., new: ...}}
    changed_at: datetime


# ---- Watchlist ----

class WatchlistDocument(BaseModel):
    """watchlist 集合文档。"""
    company_name: str
    supplier_id: str | None = None
    added_at: datetime | None = None
