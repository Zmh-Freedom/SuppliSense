"""共享测试 fixtures。"""
import os

from dotenv import load_dotenv


load_dotenv()
os.environ.setdefault("MONGO_DB", "tianyancha_test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32chars!")
os.environ.setdefault("MONGO_PASSWORD", "test_password")

import pytest
from fastapi.testclient import TestClient


_INTEGRATION_MODULES = {
    "test_agent_run_repo",
    "test_audit_repo",
    "test_auth",
    "test_alert",
    "test_company",
    "test_company_commands",
    "test_company_identity_api",
    "test_company_merge",
    "test_company_outbox_integration",
    "test_company_schema",
    "test_company_service",
    "test_company_transactions",
    "test_database_integration",
    "test_agent_harness_control_plane_integration",
    "test_agent_harness_production_e2e",
    "test_errors",
    "test_financial",
    "test_outbox_api",
    "test_outbox_service",
    "test_risk",
    "test_sourcing_risk_actions",
}


def pytest_collection_modifyitems(items):
    """Keep live-database tests out of the isolated unit/graph job."""
    integration_marker = pytest.mark.integration
    for item in items:
        module_name = item.module.__name__.rsplit(".", 1)[-1]
        if module_name in _INTEGRATION_MODULES:
            item.add_marker(integration_marker)


@pytest.fixture(autouse=True)
def test_rollout_control_plane(monkeypatch):
    from app.core import rollout_gate

    monkeypatch.setattr(rollout_gate, "_DEFAULT_STORE", rollout_gate.InMemoryRolloutStateStore())


@pytest.fixture(scope="session")
def app():
    from app.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers(client):
    """注册测试用户，返回带 token 的 auth headers。"""
    resp = client.post("/api/v1/auth/register", json={
        "username": "_testuser",
        "email": "test@test.com",
        "password": "TestPass123",
        "role": "admin",
    })
    if resp.status_code == 200:
        login_resp = client.post("/api/v1/auth/login/json", json={
            "username": "_testuser",
            "password": "TestPass123",
        })
        if login_resp.status_code == 200:
            token = login_resp.cookies.get("access_token")
            if token:
                return {"Cookie": f"access_token={token}"}
    return {}
