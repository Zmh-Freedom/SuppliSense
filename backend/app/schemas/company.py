from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.company.normalization import normalize_company_name, validate_credit_code


def _strip_and_validate_company_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    stripped_value = value.strip()
    normalized_value = normalize_company_name(stripped_value)
    if len(normalized_value) > 255:
        raise ValueError("企业名称标准化后长度不能超过 255")
    return stripped_value


def _normalize_provided_credit_code(value: object) -> object:
    if value is None or not isinstance(value, str):
        return value
    return validate_credit_code(value)


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

    @field_validator("alias_name", mode="before")
    @classmethod
    def strip_alias_name(cls, value: object) -> object:
        return _strip_and_validate_company_text(value)


class CompanyCreateInput(BaseModel):
    legal_name: str = Field(min_length=2, max_length=255)
    unified_social_credit_code: str | None = None
    registration_status: str | None = Field(default=None, max_length=32)
    verification_status: VerificationStatus = VerificationStatus.PENDING_VERIFICATION
    identity_source: str = Field(default="manual", min_length=1, max_length=32)
    source_reference: str | None = Field(default=None, max_length=255)
    aliases: list[CompanyAliasInput] = Field(default_factory=list)

    @field_validator("legal_name", mode="before")
    @classmethod
    def strip_legal_name(cls, value: object) -> object:
        return _strip_and_validate_company_text(value)

    @field_validator("unified_social_credit_code", mode="before")
    @classmethod
    def normalize_credit_code(cls, value: object) -> object:
        return _normalize_provided_credit_code(value)


class CompanyUpdateInput(BaseModel):
    expected_version: int = Field(ge=1)
    legal_name: str | None = Field(default=None, min_length=2, max_length=255)
    unified_social_credit_code: str | None = None
    registration_status: str | None = Field(default=None, max_length=32)
    identity_source: str | None = Field(default=None, min_length=1, max_length=32)
    source_reference: str | None = Field(default=None, max_length=255)

    @field_validator("legal_name", mode="before")
    @classmethod
    def strip_legal_name(cls, value: object) -> object:
        return _strip_and_validate_company_text(value)

    @field_validator("unified_social_credit_code", mode="before")
    @classmethod
    def normalize_credit_code(cls, value: object) -> object:
        return _normalize_provided_credit_code(value)


class CompanyVerifyInput(BaseModel):
    expected_version: int = Field(ge=1)
    unified_social_credit_code: str | None = None
    identity_source: Literal["tianyancha", "import", "admin_verified"]
    source_reference: str | None = Field(default=None, max_length=255)

    @field_validator("unified_social_credit_code", mode="before")
    @classmethod
    def normalize_credit_code(cls, value: object) -> object:
        return _normalize_provided_credit_code(value)


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


class CompanySearchQuery(BaseModel):
    q: str = Field(min_length=1, max_length=255, description="企业查询文本")
    limit: int = Field(default=10, ge=1, le=100, description="最大候选数")

    @field_validator("q", mode="before")
    @classmethod
    def strip_query(cls, value: object) -> object:
        return _strip_and_validate_company_text(value)


class _CompanyApiResponse(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={"additionalProperties": False},
    )


class CompanyCommandResponse(_CompanyApiResponse):
    company_id: UUID
    legal_name: str
    verification_status: VerificationStatus
    identity_version: int


class CompanyMergeResponse(_CompanyApiResponse):
    source_company_id: UUID
    target_company_id: UUID
    source_version: int
    target_version: int
    merged: bool
