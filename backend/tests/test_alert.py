"""预警端点认证测试。"""


def test_alert_watch_requires_auth(client):
    resp = client.post("/api/v1/alert/watch", json={"company_name": "测试监控公司"})
    assert resp.status_code == 401


def test_alert_watchlist_requires_auth(client):
    resp = client.get("/api/v1/alert/watchlist")
    assert resp.status_code == 401


def test_alert_status_requires_auth(client):
    resp = client.get("/api/v1/alert/status", params={"company_name": "测试公司"})
    assert resp.status_code == 401


def test_alert_check_requires_auth(client):
    resp = client.post("/api/v1/alert/check", json={"company_name": "测试公司"})
    assert resp.status_code == 401


def test_alert_unwatch_requires_auth(client):
    resp = client.delete("/api/v1/alert/watch", params={"company_name": "测试公司"})
    assert resp.status_code == 401


def test_monitor_identity_candidates_requires_auth(client):
    resp = client.get("/api/v1/alert/watch/monitor-1/identity-candidates")
    assert resp.status_code == 401


def test_monitor_identity_confirmation_requires_auth(client):
    resp = client.post(
        "/api/v1/alert/watch/monitor-1/identity-confirmation",
        json={"company_id": "company-1"},
    )
    assert resp.status_code == 401
