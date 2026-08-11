from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.company.normalization import (
    normalize_company_name,
    normalize_credit_code,
    validate_credit_code,
)
from app.schemas.company import (
    CompanyAliasInput,
    CompanyCreateInput,
    CompanyMergeInput,
    CompanyResponse,
    CompanyVerifyInput,
    IdentityResolutionResponse,
    IdentityResolutionType,
    VerificationStatus,
)


def test_normalize_company_name_uses_nfkc_casefold_and_collapses_spaces():
    assert normalize_company_name("  ＡＣＭＥ   有限公司  ") == "acme 有限公司"


def test_normalize_company_name_rejects_blank_value():
    with pytest.raises(ValueError, match="企业名称不能为空"):
        normalize_company_name(" \t ")


def test_company_text_validation_rejects_normalized_length_over_limit():
    """NFKC expansion must be constrained after normalization, not only before it."""
    with pytest.raises(ValidationError):
        CompanyCreateInput(legal_name="ﬃ" * 86)


@pytest.mark.parametrize("code", ["", "123", "91110000710925032I", "911100007109250325"])
def test_validate_credit_code_rejects_invalid_values(code):
    with pytest.raises(ValueError, match="统一社会信用代码"):
        validate_credit_code(code)


def test_validate_credit_code_accepts_valid_checksum():
    assert validate_credit_code(" 911100007109250324 ") == "911100007109250324"


def test_normalize_credit_code_returns_none_or_validated_code():
    assert normalize_credit_code(None) is None
    assert normalize_credit_code("911100007109250324") == "911100007109250324"


def test_analyst_alias_provenance_is_downgraded_before_persistence():
    """Analysts can submit aliases, but cannot manufacture trusted auto-exact evidence."""
    from app.domains.company.service import _alias_provenance_for_actor

    data = CompanyCreateInput(
        legal_name="Analyst Alias Provenance Company",
        aliases=[
            CompanyAliasInput(
                alias_name="Analyst Trusted Alias",
                alias_type="short_name",
                source="tianyancha",
                confidence=0.95,
            )
        ],
    )

    assert _alias_provenance_for_actor(data.aliases[0], "analyst") == ("manual", 0.94)


def test_company_create_input_defaults_to_pending_manual_identity():
    company = CompanyCreateInput(legal_name="示例科技有限公司")

    assert company.verification_status is VerificationStatus.PENDING_VERIFICATION
    assert company.identity_source == "manual"
    assert company.aliases == []


def test_company_schema_enforces_version_identity_source_and_merge_confirmation_inputs():
    with pytest.raises(ValidationError):
        CompanyVerifyInput(
            expected_version=0,
            identity_source="manual",
        )

    merge = CompanyMergeInput(
        target_company_id="f6aa0a3c-e72a-4a75-8749-9c7f4b6b4d24",
        source_expected_version=1,
        target_expected_version=2,
        reason="重复企业记录",
        confirm=True,
    )
    assert merge.target_company_id == UUID("f6aa0a3c-e72a-4a75-8749-9c7f4b6b4d24")
    assert merge.confirm is True


def test_company_response_and_identity_resolution_expose_persisted_identity_data():
    company_id = UUID("f6aa0a3c-e72a-4a75-8749-9c7f4b6b4d24")
    redirected_from = UUID("a6aa0a3c-e72a-4a75-8749-9c7f4b6b4d24")
    created_at = datetime(2026, 8, 3, tzinfo=timezone.utc)
    response = CompanyResponse(
        company_id=company_id,
        legal_name="示例科技有限公司",
        normalized_name="示例科技有限公司",
        unified_social_credit_code="911100007109250324",
        registration_status="存续",
        verification_status="verified",
        identity_source="admin_verified",
        source_reference=None,
        identity_version=3,
        merged_into_id=None,
        created_at=created_at,
        updated_at=created_at,
        verified_at=created_at,
        redirected_from=redirected_from,
    )
    resolution = IdentityResolutionResponse(resolution=IdentityResolutionType.CANDIDATES)

    assert response.company_id == company_id
    assert response.redirected_from == redirected_from
    assert resolution.exact is None
    assert resolution.candidates == []
