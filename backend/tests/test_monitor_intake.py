from unittest.mock import MagicMock

from bson import ObjectId

from app.domains.alert import intake_service


def test_monitor_intake_collects_evidence_without_creating_watchlist(monkeypatch):
    db = {"monitor_intakes": MagicMock()}
    candidate = {
        "candidate_id": "supplier:s-1", "candidate_type": "supplier", "supplier_id": "s-1",
        "supplier_code": "SUP-001", "company_id": "c-1", "legal_name": "青岛三祥科技股份有限公司",
        "verification_status": "verified", "match_type": "legal_name", "confidence": 1.0, "source": "内部供应商库",
    }
    monkeypatch.setattr(intake_service, "get_db", lambda: db)
    monkeypatch.setattr(intake_service, "_load_local_candidates", lambda query: [candidate])
    monkeypatch.setattr(intake_service, "_load_external_profile", lambda query: ({"company_name": query}, {"key": "enterprise", "label": "企业工商与风险", "status": "available", "detail": "已取得"}))
    monkeypatch.setattr(intake_service, "_data_coverage", lambda selected, query, state: ([{"key": "transaction", "label": "内部月度交易", "status": "available", "detail": "已关联"}], [{"title": "已关联内部交易", "evidence": "2026-08", "status": "supported"}], []))

    result = intake_service.create_monitor_intake("青岛三祥", "user-1")

    assert result["status"] == "ready_for_selection"
    assert result["selected_candidate_id"] == "supplier:s-1"
    assert result["findings"][0]["title"] == "已关联内部交易"
    assert db["monitor_intakes"].insert_one.called


def test_monitor_intake_confirmation_creates_target_and_baseline(monkeypatch):
    db = {"monitor_intakes": MagicMock()}
    document = {
        "_id": "mongo-1", "intake_id": "intake-1", "created_by": "user-1", "query": "青岛三祥",
        "selected_candidate_id": "supplier:s-1", "candidates": [{"candidate_id": "supplier:s-1", "legal_name": "青岛三祥科技股份有限公司", "supplier_id": "s-1", "supplier_code": "SUP-001", "company_id": "c-1"}],
    }
    db["monitor_intakes"].find_one.return_value = document
    target = {"monitor_target_id": "target-1", "company_name": "青岛三祥科技股份有限公司", "target_type": "formal_supplier", "supplier_id": "s-1", "company_id": "c-1"}
    preview = object()
    monkeypatch.setattr(intake_service, "get_db", lambda: db)
    monkeypatch.setattr("app.domains.alert.service.add_to_watchlist", lambda *args, **kwargs: target)
    monkeypatch.setattr(
        "app.domains.alert.service.get_watchlist_target_summaries",
        lambda *_args, **_kwargs: [target],
    )
    monkeypatch.setattr("app.domains.alert.service.save_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.domains.risk.service.calculate_company_risk_preview", lambda name: preview)

    result = intake_service.confirm_monitor_intake("intake-1", "user-1")

    assert result["status"] == "confirmed"
    assert result["monitor_target"]["monitor_target_id"] == "target-1"
    assert result["baseline_status"] == "created"
    assert db["monitor_intakes"].update_one.called


def test_monitor_intake_api_requires_auth(client):
    assert client.post("/api/v1/alert/intakes", json={"query": "青岛三祥"}).status_code == 401
    assert client.get("/api/v1/alert/intakes/intake-1").status_code == 401
    assert client.post("/api/v1/alert/intakes/intake-1/selection", json={"candidate_id": "supplier:s-1"}).status_code == 401
    assert client.post("/api/v1/alert/intakes/intake-1/confirmation").status_code == 401


def test_monitor_intake_serializes_mongo_object_id():
    assert intake_service._serialize({"_id": ObjectId("64b64c6a2f1f2d3e4a5b6c7d")}) == {
        "_id": "64b64c6a2f1f2d3e4a5b6c7d"
    }


def test_monitor_intake_reads_current_feishu_supplier_master(monkeypatch):
    db = {"supplier_master_snapshots": MagicMock(), "suppliers": MagicMock()}
    snapshots = db["supplier_master_snapshots"]
    snapshots.find.return_value.limit.return_value = [{
        "supplier_id": "supplier:feishu:tri-sam", "supplier_code": "8370069",
        "name": "青岛三祥科技股份有限公司", "source": "feishu_bitable", "sync_status": "current",
    }]
    monkeypatch.setattr(intake_service, "get_db", lambda: db)
    monkeypatch.setattr("app.domains.company.service.search_identity", lambda *_args, **_kwargs: {"exact": None, "candidates": []})
    monkeypatch.setattr("app.domains.sourcing.supplier_repo.has_current_feishu_supplier_snapshot", lambda database: True)

    candidates = intake_service._load_local_candidates("青岛三祥科技股份有限公司")

    assert candidates[0]["supplier_id"] == "supplier:feishu:tri-sam"
    assert candidates[0]["supplier_code"] == "8370069"
    assert candidates[0]["source"] == "飞书正式供应商主数据"
    assert snapshots.find.called
