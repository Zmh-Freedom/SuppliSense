from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CompanyProfile(BaseModel):
    company_name: str
    legal_person: str
    registered_capital: str
    establish_time: str
    is_listed: bool
    industry: str = ""


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    PENDING_VERIFICATION = "pending_verification"


class IdentityResolutionType(str, Enum):
    EXACT = "exact"
    CANDIDATES = "candidates"
    PENDING_VERIFICATION = "pending_verification"


class CompanyAliasInput(BaseModel):
    alias_name: str = Field(min_length=1, max_length=255)
    alias_type: Literal["short_name", "former_name", "english_name", "source_name"]
    source: str = Field(default="manual", min_length=1, max_length=32)
    confidence: float = Field(default=1.0, ge=0, le=1)


class CompanyCreateInput(BaseModel):
    legal_name: str = Field(min_length=2, max_length=255)
    unified_social_credit_code: str | None = None
    registration_status: str | None = Field(default=None, max_length=32)
    verification_status: VerificationStatus = VerificationStatus.PENDING_VERIFICATION
    identity_source: str = Field(default="manual", min_length=1, max_length=32)
    source_reference: str | None = Field(default=None, max_length=255)
    aliases: list[CompanyAliasInput] = Field(default_factory=list)


class CompanyUpdateInput(BaseModel):
    expected_version: int = Field(ge=1)
    legal_name: str | None = Field(default=None, min_length=2, max_length=255)
    unified_social_credit_code: str | None = None
    registration_status: str | None = Field(default=None, max_length=32)
    identity_source: str | None = Field(default=None, min_length=1, max_length=32)
    source_reference: str | None = Field(default=None, max_length=255)


class CompanyVerifyInput(BaseModel):
    expected_version: int = Field(ge=1)
    unified_social_credit_code: str | None = None
    identity_source: Literal["tianyancha", "import", "admin_verified"]
    source_reference: str | None = Field(default=None, max_length=255)


class CompanyMergeInput(BaseModel):
    target_company_id: UUID
    source_expected_version: int = Field(ge=1)
    target_expected_version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=500)
    confirm: bool


class CompanyResponse(BaseModel):
    company_id: UUID
    legal_name: str
    normalized_name: str
    unified_social_credit_code: str | None
    registration_status: str | None
    verification_status: VerificationStatus
    identity_source: str
    source_reference: str | None
    identity_version: int
    merged_into_id: UUID | None
    created_at: datetime
    updated_at: datetime
    verified_at: datetime | None
    redirected_from: UUID | None = None


class CompanyCandidate(BaseModel):
    company_id: UUID
    legal_name: str
    unified_social_credit_code: str | None
    registration_status: str | None
    verification_status: VerificationStatus
    match_type: Literal["credit_code", "legal_name", "alias", "prefix"]
    confidence: float
    redirected_from: UUID | None = None


class IdentityResolutionResponse(BaseModel):
    resolution: IdentityResolutionType
    exact: CompanyCandidate | None = None
    candidates: list[CompanyCandidate] = Field(default_factory=list)
