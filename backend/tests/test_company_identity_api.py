"""Company Identity API behaviour tests."""

import importlib
from datetime import datetime, timezone

import pytest

from app.core.deps import get_current_user
from app.core.errors import DomainError
from app.schemas.user import UserInDB, UserRole


COMPANY_ID = "00000000-0000-0000-0000-000000000001"
TARGET_COMPANY_ID = "00000000-0000-0000-0000-000000000002"
ACTOR_ID = "00000000-0000-0000-0000-000000000010"


def _company() -> dict:
    return {
        "id": COMPANY_ID,
        "legal_name": "示例科技有限公司",
        "normalized_name": "示例科技有限公司",
        "unified_social_credit_code": "911100007109250324",
        "registration_status": "active",
        "verification_status": "verified",
        "identity_source": "admin_verified",
        "source_reference": "registry-1",
        "identity_version": 2,
        "merged_into_id": None,
        "created_at": datetime(2026, 8, 3, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 3, tzinfo=timezone.utc),
        "verified_at": datetime(2026, 8, 3, tzinfo=timezone.utc),
        "redirected_from": None,
    }


def _user(role: UserRole, user_id: str = ACTOR_ID) -> UserInDB:
    return UserInDB(
        id=user_id,
        username=f"{role.value}-user",
        email=f"{role.value}@example.com",
        role=role,
        password_hash="unused",
        created_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
        is_active=True,
    )


@pytest.fixture
def as_role(app, client):
    def set_role(role: UserRole, user_id: str = ACTOR_ID):
        app.dependency_overrides[get_current_user] = lambda: _user(role, user_id)
        return client

    yield set_role
    app.dependency_overrides.clear()


@pytest.fixture
def company_api():
    return importlib.import_module("app.domains.company.api")


@pytest.fixture
def offloaded(monkeypatch, company_api):
    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    async def run_in_test_thread(function, /, *args, **kwargs):
        calls.append((function, args, kwargs))
        return function(*args, **kwargs)

    monkeypatch.setattr(company_api.asyncio, "to_thread", run_in_test_thread)
    return calls


@pytest.mark.parametrize(
    ("method", "path", "params", "body"),
    [
        ("get", "/api/v1/companies/search", {"q": "示例"}, None),
        ("get", f"/api/v1/companies/{COMPANY_ID}", None, None),
        ("post", "/api/v1/companies", None, {"legal_name": "示例科技有限公司"}),
        (
            "patch",
            f"/api/v1/companies/{COMPANY_ID}",
            None,
            {"expected_version": 1, "registration_status": "active"},
        ),
        (
            "post",
            f"/api/v1/companies/{COMPANY_ID}/verify",
            None,
            {"expected_version": 1, "identity_source": "admin_verified"},
        ),
        (
            "post",
            f"/api/v1/companies/{COMPANY_ID}/merge",
            None,
            {
                "target_company_id": TARGET_COMPANY_ID,
                "source_expected_version": 1,
                "target_expected_version": 1,
                "reason": "重复企业记录",
                "confirm": True,
            },
        ),
    ],
)
def test_company_endpoints_require_auth(client, method, path, params, body):
    """Removing JWT authentication must deny every company identity operation."""
    response = client.request(method.upper(), path, params=params, json=body)
    assert response.status_code == 401


def test_company_search_is_registered_before_company_id_route(client):
    """A literal search path must not be handled as a company ID lookup."""
    response = client.get("/api/v1/companies/search", params={"q": "示例"})
    assert response.status_code == 401


def test_company_search_allows_viewer_and_offloads_service(as_role, monkeypatch, company_api, offloaded):
    """A viewer can search and the synchronous resolver runs through the thread boundary."""
    monkeypatch.setattr(
        company_api,
        "search_identity",
        lambda query, limit: {"resolution": "candidates", "exact": None, "candidates": []},
    )

    response = as_role(UserRole.VIEWER).get("/api/v1/companies/search", params={"q": "示例"})

    assert response.status_code == 200
    assert response.json() == {"resolution": "candidates", "exact": None, "candidates": []}
    assert offloaded[0][1] == ("示例", 10)


def test_get_company_returns_canonical_response_and_offloads_service(as_role, monkeypatch, company_api, offloaded):
    """An authenticated reader receives the canonical company response for its ID."""
    monkeypatch.setattr(company_api, "get_company", lambda company_id: _company())

    response = as_role(UserRole.VIEWER).get(f"/api/v1/companies/{COMPANY_ID}")

    assert response.status_code == 200
    assert response.json()["company_id"] == COMPANY_ID
    assert response.json()["identity_version"] == 2
    assert offloaded[0][1] == (COMPANY_ID,)


def test_get_company_not_found_uses_domain_error_envelope(as_role, monkeypatch, company_api):
    """A missing company must keep the global business-error response contract."""
    monkeypatch.setattr(company_api, "get_company", lambda company_id: None)

    response = as_role(UserRole.VIEWER).get(f"/api/v1/companies/{COMPANY_ID}")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "COMPANY_NOT_FOUND",
            "message": "企业不存在",
            "detail": None,
        }
    }


@pytest.mark.parametrize(
    ("role", "method", "path", "body"),
    [
        (UserRole.VIEWER, "post", "/api/v1/companies", {"legal_name": "示例科技有限公司"}),
        (
            UserRole.VIEWER,
            "patch",
            f"/api/v1/companies/{COMPANY_ID}",
            {"expected_version": 1, "registration_status": "active"},
        ),
        (
            UserRole.ANALYST,
            "post",
            f"/api/v1/companies/{COMPANY_ID}/verify",
            {"expected_version": 1, "identity_source": "admin_verified"},
        ),
        (
            UserRole.ANALYST,
            "post",
            f"/api/v1/companies/{COMPANY_ID}/merge",
            {
                "target_company_id": TARGET_COMPANY_ID,
                "source_expected_version": 1,
                "target_expected_version": 1,
                "reason": "重复企业记录",
                "confirm": True,
            },
        ),
    ],
)
def test_company_write_role_matrix_denies_unauthorized_roles(as_role, role, method, path, body):
    """Viewers cannot write, and analysts cannot verify or merge companies."""
    response = getattr(as_role(role), method)(path, json=body)
    assert response.status_code == 403


def test_create_company_allows_analyst_and_uses_dependency_actor(as_role, monkeypatch, company_api, offloaded):
    """Create passes only the authenticated analyst identity into the service."""
    captured: dict[str, object] = {}

    def create(data, actor_id, actor_role):
        captured.update(actor_id=actor_id, actor_role=actor_role, legal_name=data.legal_name)
        return {
            "company_id": COMPANY_ID,
            "legal_name": data.legal_name,
            "verification_status": "pending_verification",
            "identity_version": 1,
        }

    monkeypatch.setattr(company_api, "create_company", create)
    response = as_role(UserRole.ANALYST, "00000000-0000-0000-0000-000000000020").post(
        "/api/v1/companies",
        json={"legal_name": "示例科技有限公司", "actor_id": "untrusted-request-actor"},
    )

    assert response.status_code == 200
    assert response.json()["company_id"] == COMPANY_ID
    assert captured == {
        "actor_id": "00000000-0000-0000-0000-000000000020",
        "actor_role": "analyst",
        "legal_name": "示例科技有限公司",
    }
    assert offloaded[0][1][1:] == ("00000000-0000-0000-0000-000000000020", "analyst")


def test_update_company_allows_admin_and_uses_dependency_actor(as_role, monkeypatch, company_api, offloaded):
    """Update forwards the authenticated admin rather than a request-supplied actor."""
    captured: dict[str, object] = {}

    def update(company_id, data, actor_id, actor_role):
        captured.update(company_id=company_id, actor_id=actor_id, actor_role=actor_role)
        return {
            "company_id": company_id,
            "legal_name": "示例科技有限公司",
            "verification_status": "pending_verification",
            "identity_version": 2,
        }

    monkeypatch.setattr(company_api, "update_company", update)
    response = as_role(UserRole.ADMIN).patch(
        f"/api/v1/companies/{COMPANY_ID}",
        json={"expected_version": 1, "registration_status": "active", "actor_role": "viewer"},
    )

    assert response.status_code == 200
    assert captured == {"company_id": COMPANY_ID, "actor_id": ACTOR_ID, "actor_role": "admin"}
    assert offloaded[0][1][2:] == (ACTOR_ID, "admin")


def test_verify_company_requires_admin_and_offloads_service(as_role, monkeypatch, company_api, offloaded):
    """An administrator can verify and is recorded as the service actor."""
    captured: dict[str, object] = {}

    def verify(company_id, data, actor_id, actor_role):
        captured.update(company_id=company_id, actor_id=actor_id, actor_role=actor_role)
        return {
            "company_id": company_id,
            "legal_name": "示例科技有限公司",
            "verification_status": "verified",
            "identity_version": 2,
        }

    monkeypatch.setattr(company_api, "verify_company", verify)
    response = as_role(UserRole.ADMIN).post(
        f"/api/v1/companies/{COMPANY_ID}/verify",
        json={"expected_version": 1, "identity_source": "admin_verified"},
    )

    assert response.status_code == 200
    assert captured == {"company_id": COMPANY_ID, "actor_id": ACTOR_ID, "actor_role": "admin"}
    assert offloaded[0][1][2:] == (ACTOR_ID, "admin")


def test_merge_company_requires_admin_and_offloads_service(as_role, monkeypatch, company_api, offloaded):
    """An administrator can merge a confirmed pair and is recorded as the actor."""
    captured: dict[str, object] = {}

    def merge(company_id, data, actor_id, actor_role):
        captured.update(company_id=company_id, actor_id=actor_id, actor_role=actor_role)
        return {
            "source_company_id": company_id,
            "target_company_id": str(data.target_company_id),
            "source_version": 2,
            "target_version": 3,
            "merged": True,
        }

    monkeypatch.setattr(company_api, "merge_company", merge)
    response = as_role(UserRole.ADMIN).post(
        f"/api/v1/companies/{COMPANY_ID}/merge",
        json={
            "target_company_id": TARGET_COMPANY_ID,
            "source_expected_version": 1,
            "target_expected_version": 2,
            "reason": "重复企业记录",
            "confirm": True,
        },
    )

    assert response.status_code == 200
    assert captured == {"company_id": COMPANY_ID, "actor_id": ACTOR_ID, "actor_role": "admin"}
    assert offloaded[0][1][2:] == (ACTOR_ID, "admin")


def test_update_domain_error_keeps_business_error_envelope(as_role, monkeypatch, company_api):
    """Service conflicts must propagate through the registered DomainError handler."""
    def conflict(*args, **kwargs):
        raise DomainError(
            "COMPANY_VERSION_CONFLICT",
            "企业版本冲突",
            409,
            {"expected_version": 2},
        )

    monkeypatch.setattr(company_api, "update_company", conflict)
    response = as_role(UserRole.ADMIN).patch(
        f"/api/v1/companies/{COMPANY_ID}",
        json={"expected_version": 2, "registration_status": "active"},
    )

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "COMPANY_VERSION_CONFLICT",
        "message": "企业版本冲突",
        "detail": {"expected_version": 2},
    }


def test_company_api_rejects_invalid_search_and_command_inputs(as_role):
    """Invalid query, optimistic version, and merge IDs use the validation envelope."""
    client = as_role(UserRole.ADMIN)

    missing_query = client.get("/api/v1/companies/search")
    invalid_update = client.patch(
        f"/api/v1/companies/{COMPANY_ID}",
        json={"expected_version": 0, "registration_status": "active"},
    )
    invalid_merge = client.post(
        f"/api/v1/companies/{COMPANY_ID}/merge",
        json={
            "target_company_id": "not-a-uuid",
            "source_expected_version": 1,
            "target_expected_version": 1,
            "reason": "重复企业记录",
            "confirm": True,
        },
    )

    for response in (missing_query, invalid_update, invalid_merge):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_company_api_openapi_declares_companies_tag_and_contract(app):
    """OpenAPI must expose all six company identity operations under their own tag."""
    schema = app.openapi()

    assert {tag["name"] for tag in schema["tags"]} >= {"companies"}
    assert set(schema["paths"]["/api/v1/companies/search"]) == {"get"}
    assert set(schema["paths"]["/api/v1/companies"]) == {"post"}
    assert set(schema["paths"][f"/api/v1/companies/{{company_id}}"]) == {"get", "patch"}
    assert set(schema["paths"][f"/api/v1/companies/{{company_id}}/verify"]) == {"post"}
    assert set(schema["paths"][f"/api/v1/companies/{{company_id}}/merge"]) == {"post"}
    assert schema["paths"]["/api/v1/companies/search"]["get"]["tags"] == ["companies"]
    assert schema["paths"]["/api/v1/companies"]["post"]["summary"] == "创建企业身份主体"
