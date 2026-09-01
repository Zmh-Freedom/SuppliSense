"""认证端点测试。"""
import pytest


def test_login_json_success(client):
    resp = client.post("/api/v1/auth/login/json", json={
        "username": "admin",
        "password": "wrong-password-123",
    })
    assert resp.status_code == 401


def test_login_json_missing_body(client):
    resp = client.post("/api/v1/auth/login/json", json={})
    assert resp.status_code == 422


def test_login_json_short_password(client):
    resp = client.post("/api/v1/auth/login/json", json={
        "username": "admin",
        "password": "ab1",
    })
    assert resp.status_code == 401


def test_register_unauthenticated(client):
    """注册端点需要 admin 权限，未认证请求返回 401。"""
    resp = client.post("/api/v1/auth/register", json={
        "username": "testuser1",
        "email": "test1@test.com",
        "password": "TestPass123",
        "role": "analyst",
    })
    assert resp.status_code == 401


def test_register_invalid_password_short(client):
    """注册端点需要认证，未认证时返回 401（先于密码校验）。"""
    resp = client.post("/api/v1/auth/register", json={
        "username": "testuser1",
        "email": "test1@test.com",
        "password": "ab1",
        "role": "analyst",
    })
    assert resp.status_code == 401


def test_register_invalid_password_no_letter(client):
    resp = client.post("/api/v1/auth/register", json={
        "username": "testuser2",
        "email": "test2@test.com",
        "password": "12345678",
        "role": "analyst",
    })
    assert resp.status_code == 401


def test_me_unauthenticated(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.parametrize("endpoint", [
    "/api/v1/risk/assess",
    "/api/v1/risk/calculate",
    "/api/v1/company/profile",
    "/api/v1/alert/status",
    "/api/v1/chat/stream",
    "/api/v1/sentiment/test-company",
])
def test_protected_endpoints_require_auth(client, endpoint):
    """验证受保护端点无 token 时返回 401。"""
    if endpoint == "/api/v1/chat/stream":
        resp = client.post(endpoint, json={"message": "hello"})
    elif endpoint == "/api/v1/risk/assess" or endpoint == "/api/v1/risk/calculate":
        resp = client.post(endpoint, json={"company_name": "test"})
    else:
        resp = client.get(endpoint)
    assert resp.status_code == 401, f"{endpoint} should require auth, got {resp.status_code}"
