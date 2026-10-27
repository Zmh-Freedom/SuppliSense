from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
import pytest

from app.core.deps import get_current_user
from app.core.errors import (
    DomainError,
    domain_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.domains.risk import api_scenario, api_screening
from app.domains.supplier import api as supplier_api
from app.schemas.screening import ScreeningReadResponse, SupplierComplianceReadResponse


ACTOR_ID = "00000000-0000-0000-0000-000000000101"
SUPPLIER_ID = "supplier-fixture-a"
COMPANY_NAME = "虚构甲供应链有限公司"


def _user():
    return SimpleNamespace(
        id=ACTOR_ID,
        role=SimpleNamespace(value="purchaser"),
    )


def _make_app(*, authenticated=True):
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(api_scenario.router, prefix="/analysis")
    api_v1.include_router(api_screening.router)
    api_v1.include_router(supplier_api.router)
    app.include_router(api_v1)
    if authenticated:
        app.dependency_overrides[get_current_user] = _user
    return app


@pytest.fixture
def screening_client(monkeypatch):
    with TestClient(_make_app()) as client:
        yield client


def _unavailable_screening() -> dict:
    return {
        "schema_version": 1,
        "company_name": COMPANY_NAME,
        "entity_id": "company-fixture-a",
        "scope": "fixture-scope",
        "screening_status": "unavailable",
        "coverage": {"status": "insufficient", "sources": [], "required_sources": []},
        "matches": [],
        "domestic_findings": [],
        "limitations": ["当前未接入经确认的正式名单来源，不能完成筛查。"],
        "checked_at": datetime(2026, 10, 23, tzinfo=timezone.utc).isoformat(),
        "legacy_projection_policy": None,
    }


def test_new_screening_endpoint_returns_schema_valid_unavailable_state(monkeypatch, screening_client):
    observed_contexts = []

    def analyze(company_name):
        from app.domains.risk.assessment_contracts import get_assessment_context

        observed_contexts.append(get_assessment_context())
        return {**_unavailable_screening(), "company_name": company_name}

    monkeypatch.setattr(api_screening, "analyze_sanctions", analyze)

    response = screening_client.get(f"/api/v1/sanctions-screenings/{COMPANY_NAME}")

    assert response.status_code == 200
    assert ScreeningReadResponse.model_validate(response.json()).screening_status == "unavailable"
    assert observed_contexts[0]["actor_id"] == ACTOR_ID
    assert observed_contexts[0]["actor_role"] == "purchaser"


def test_screening_endpoint_requires_authentication():
    with TestClient(_make_app(authenticated=False)) as client:
        response = client.get(f"/api/v1/sanctions-screenings/{COMPANY_NAME}")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "HTTP_401"


def test_screening_endpoint_requires_stable_entity_identity(monkeypatch, screening_client):
    monkeypatch.setattr(
        api_screening,
        "analyze_sanctions",
        lambda company_name: {
            **_unavailable_screening(),
            "company_name": company_name,
            "entity_id": None,
        },
    )

    response = screening_client.get(f"/api/v1/sanctions-screenings/{COMPANY_NAME}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "HTTP_409"


def test_legacy_screening_unavailable_returns_existing_error_envelope(monkeypatch, screening_client):
    monkeypatch.setattr(
        api_scenario,
        "check_sanctions",
        lambda _name: (_ for _ in ()).throw(ValueError("当前名单筛查状态无法安全转换为旧响应")),
    )

    response = screening_client.get(f"/api/v1/analysis/sanctions/{COMPANY_NAME}")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "HTTP_503"


def test_legacy_dashboard_returns_503_when_any_company_cannot_be_projected(monkeypatch, screening_client):
    monkeypatch.setattr(
        api_scenario,
        "get_sanctions_dashboard",
        lambda *_args: (_ for _ in ()).throw(ValueError("当前名单筛查状态无法安全转换为旧响应")),
    )

    response = screening_client.get("/api/v1/analysis/sanctions")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "HTTP_503"


def test_supplier_compliance_endpoint_preserves_domestic_findings(monkeypatch, screening_client):
    supplier = {
        "_id": SUPPLIER_ID,
        "supplier_id": SUPPLIER_ID,
        "name": COMPANY_NAME,
        "company_id": "company-fixture-a",
    }
    domestic = [{
        "kind": "lawsuit",
        "count": 2,
        "source": "lawSuit",
        "validity": {"status": "available", "observed_at": "2026-10-22T00:00:00+00:00"},
    }]
    monkeypatch.setattr("app.domains.supplier.repo.get_supplier", lambda _supplier_id: supplier)
    monkeypatch.setattr(supplier_api, "can_access_supplier", lambda *_args: True)

    def build_status(_supplier_id):
        from app.domains.risk.assessment_contracts import get_assessment_context

        assert get_assessment_context()["actor_id"] == ACTOR_ID
        return {
            "schema_version": 1,
            "screening": _unavailable_screening(),
            "domestic_findings": domestic,
            "limitations": ["名单来源未配置；国内诉讼来源已核验。"],
        }

    monkeypatch.setattr(
        "app.domains.supplier.compliance_service.build_supplier_compliance_status",
        build_status,
    )

    response = screening_client.get(f"/api/v1/suppliers/{SUPPLIER_ID}/compliance-status")

    assert response.status_code == 200
    result = SupplierComplianceReadResponse.model_validate(response.json())
    assert result.domestic_findings[0]["count"] == 2
    assert result.screening.screening_status == "unavailable"


def test_supplier_compliance_endpoint_rejects_out_of_scope_supplier(monkeypatch, screening_client):
    supplier = {"_id": SUPPLIER_ID, "supplier_id": SUPPLIER_ID, "name": COMPANY_NAME}
    monkeypatch.setattr("app.domains.supplier.repo.get_supplier", lambda _supplier_id: supplier)
    monkeypatch.setattr(supplier_api, "can_access_supplier", lambda *_args: False)
    called = []
    monkeypatch.setattr(
        "app.domains.supplier.compliance_service.build_supplier_compliance_status",
        lambda *_args, **_kwargs: called.append(True),
    )

    response = screening_client.get(f"/api/v1/suppliers/{SUPPLIER_ID}/compliance-status")

    assert response.status_code == 403
    assert called == []


def test_supplier_compliance_endpoint_returns_404_for_missing_supplier(monkeypatch, screening_client):
    monkeypatch.setattr("app.domains.supplier.repo.get_supplier", lambda _supplier_id: None)

    response = screening_client.get(f"/api/v1/suppliers/{SUPPLIER_ID}/compliance-status")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "HTTP_404"
