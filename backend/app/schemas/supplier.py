"""Supplier schemas — master data management and profile aggregation."""

from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Master Data
# ---------------------------------------------------------------------------


class SupplierUpdateInput(BaseModel):
    """供应商主数据更新请求，所有字段可选。"""

    name: str | None = None
    unified_code: str | None = None
    legal_person: str | None = None
    registered_capital: str | None = None
    establish_time: str | None = None
    reg_status: str | None = None
    industry: str | None = None
    categories: list[str] | None = None
    regions: list[str] | None = None
    contact_person: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    website_url: str | None = None
    address: str | None = None
    scale: str | None = None
    description: str | None = None
    certifications: list[dict] | None = None
    annual_revenue: float | None = None
    credit_rating: str | None = None
    status: str | None = None


class SupplierMasterResponse(BaseModel):
    """供应商主数据完整响应。"""

    id: str = Field(alias="_id")
    name: str
    unified_code: str | None = None
    legal_person: str | None = None
    registered_capital: str | None = None
    establish_time: str | None = None
    reg_status: str | None = None
    industry: str | None = None
    categories: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    contact_person: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    website_url: str | None = None
    address: str | None = None
    scale: str | None = None
    description: str | None = None
    certifications: list[dict] = Field(default_factory=list)
    annual_revenue: float | None = None
    credit_rating: str | None = None
    status: str = "prospective"
    source: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SupplierListResponse(BaseModel):
    """供应商分页列表。"""

    items: list[SupplierMasterResponse]
    total: int
    page: int = 1
    page_size: int = 20


# ---------------------------------------------------------------------------
# Change Log
# ---------------------------------------------------------------------------


class ChangelogEntry(BaseModel):
    """变更记录条目。"""

    changed: dict  # {field_name: {old: ..., new: ...}}
    changed_at: datetime


# ---------------------------------------------------------------------------
# Profile (aggregation) schemas
# ---------------------------------------------------------------------------


class ProfileBasicInfo(BaseModel):
    """画像 — 基本信息。"""

    name: str
    unified_code: str | None = None
    legal_person: str | None = None
    registered_capital: str | None = None
    establish_time: str | None = None
    reg_status: str | None = None
    industry: str | None = None
    categories: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    scale: str | None = None
    address: str | None = None
    contact_person: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    website_url: str | None = None
    source: str | None = None
    updated_at: str | None = None
    industry_source: str | None = None
    industry_updated_at: str | None = None
    website_url_source: str | None = None
    contact_phone_source: str | None = None
    contact_email_source: str | None = None
    status: str = "prospective"


class ProfileRiskSummary(BaseModel):
    """画像 — 风险概览。"""

    risk_score: int
    risk_level: str
    trend: list[dict] = Field(default_factory=list)  # [{date, risk_score, risk_level}]
    alert_count: int = 0
    last_checked: str | None = None
    in_watchlist: bool = False


class ProfileFinancialSnapshot(BaseModel):
    """画像 — 财务快照。"""

    revenue_growth: float | None = None
    net_profit_growth: float | None = None
    debt_ratio: float | None = None
    cash_flow: float | None = None
    roe: float | None = None
    net_profit_margin: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None
    credit_rating: str | None = None
    annual_revenue: float | None = None
    cached_at: str | None = None
    history: list[dict] = Field(default_factory=list)


class ProfileSentimentSummary(BaseModel):
    """画像 — 舆情摘要。"""

    overall_sentiment: str = "未知"  # 正面 / 中性 / 负面
    sentiment_score: float = 0
    negative_ratio: float = 0
    article_count: int = 0
    top_tags: list[str] = Field(default_factory=list)
    analyzed_at: str | None = None


class ProfileComplianceStatus(BaseModel):
    """画像 — 合规状态。"""

    sanctions_clean: bool = True
    sanctions_match_count: int = 0
    lawsuit_count: int = 0
    executed_count: int = 0
    dishonesty_count: int = 0
    abnormal_operation_count: int = 0
    administrative_penalty_count: int = 0
    tax_arrears_count: int = 0


class ProfileESGSummary(BaseModel):
    """画像 — ESG 评分。"""

    environmental: dict | None = None  # {score, level, detail}
    social: dict | None = None
    governance: dict | None = None


class ProfileAlertItem(BaseModel):
    """画像 — 告警条目。"""

    id: str = Field(alias="_id")
    company_name: str | None = None
    severity: str
    changes: list[dict] = Field(default_factory=list)
    created_at: str


class ProfileRelationshipSummary(BaseModel):
    """画像 — 关联关系。"""

    related_count: int = 0
    branch_count: int = 0
    dependency_count: int = 0
    high_risk_related_count: int = 0
    entities: list[dict] = Field(default_factory=list)


class SupplierProfileResponse(BaseModel):
    """供应商完整画像（聚合所有领域数据）。"""

    basic_info: ProfileBasicInfo
    risk: ProfileRiskSummary | None = None
    financial: ProfileFinancialSnapshot | None = None
    sentiment: ProfileSentimentSummary | None = None
    compliance: ProfileComplianceStatus | None = None
    esg: ProfileESGSummary | None = None
    alerts: list[ProfileAlertItem] = Field(default_factory=list)
    relationships: ProfileRelationshipSummary | None = None
    changelog: list[ChangelogEntry] = Field(default_factory=list)
