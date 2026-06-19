"""共享测试 fixtures。"""
import os

os.environ.setdefault("MONGO_DB", "tianyancha_test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32chars!")
os.environ.setdefault("MONGO_PASSWORD", "test_password")

import pytest
from fastapi.testclient import TestClient


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
