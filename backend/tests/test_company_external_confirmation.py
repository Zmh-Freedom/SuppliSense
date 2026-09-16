from contextlib import nullcontext
from unittest.mock import Mock

from app.domains.company import repo as company_repo
from app.domains.company import service as company_service


PROFILE = {
    "company_name": "外部主体有限公司",
    "unified_social_credit_code": "91450200MAA7L76A5R",
    "registration_status": "存续",
    "legal_person": "张三",
    "source_reference": "tyc:外部主体有限公司",
}


def _company(company_id: str = "company-1", status: str = "verified") -> dict:
    return {
        "id": company_id,
        "legal_name": "外部主体有限公司",
        "verification_status": status,
        "identity_version": 1,
        "unified_social_credit_code": "91450200MAA7L76A5R",
        "merged_into_id": None,
    }


def test_purchaser_can_confirm_external_profile_and_create_canonical_company(monkeypatch):
    cursor = Mock()
    created = _company()
    monkeypatch.setattr(company_service, "get_cursor", lambda: nullcontext((None, cursor)))
    monkeypatch.setattr(company_repo, "find_by_credit_code_with_cursor", lambda *_: None)
    monkeypatch.setattr(company_repo, "insert_company", lambda *_args, **_kwargs: created)
    monkeypatch.setattr(company_service, "_write_audit_and_event", lambda *_args: None)

    result = company_service.confirm_external_company(PROFILE, "buyer-1", "purchaser")

    assert result == {
        "company_id": "company-1",
        "legal_name": "外部主体有限公司",
        "verification_status": "verified",
        "identity_version": 1,
    }


def test_external_confirmation_reuses_already_verified_credit_code(monkeypatch):
    cursor = Mock()
    existing = _company()
    monkeypatch.setattr(company_service, "get_cursor", lambda: nullcontext((None, cursor)))
    monkeypatch.setattr(company_repo, "find_by_credit_code_with_cursor", lambda *_: existing)
    audit = Mock()
    monkeypatch.setattr(company_service, "_write_audit_and_event", audit)

    result = company_service.confirm_external_company(PROFILE, "buyer-1", "purchaser")

    assert result["company_id"] == "company-1"
    audit.assert_not_called()
