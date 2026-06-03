def test_watch_and_unwatch(client):
    resp = client.post("/alert/watch", json={"company_name": "测试监控公司"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "watching"

    resp = client.get("/alert/watchlist")
    assert resp.status_code == 200
    assert "测试监控公司" in resp.json()["companies"]

    resp = client.delete("/alert/watch", params={"company_name": "测试监控公司"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"


def test_check_no_history(client):
    resp = client.post("/alert/check", json={"company_name": "从未评估过的公司"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["changed"] is False
    assert "暂无历史快照" in data.get("message", "")


def test_status_no_history(client):
    resp = client.get("/alert/status", params={"company_name": "不存在的公司"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_snapshot"] is False
