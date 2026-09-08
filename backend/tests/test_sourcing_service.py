"""Tests for sourcing service boundaries and hard category gating."""

from unittest.mock import MagicMock

from app.domains.sourcing.service import (
    _filter_category_candidates,
    approve_application,
    reject_application,
    search_suppliers,
    select_external_candidate,
    verify_external_candidate,
    _search_internal_history_candidates,
)


def test_nonexistent_approve_raises(monkeypatch):
    """approve_application raises ValueError when app not found."""
    mock_repo = MagicMock()
    mock_repo.get_access_application.return_value = None
    monkeypatch.setattr(
        "app.domains.sourcing.service.get_access_application",
        mock_repo.get_access_application,
    )
    try:
        approve_application("nonexistent-id", "reviewer1")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "不存在" in str(e)


def test_nonexistent_reject_raises(monkeypatch):
    """reject_application raises ValueError when app not found."""
    mock_repo = MagicMock()
    mock_repo.get_access_application.return_value = None
    monkeypatch.setattr(
        "app.domains.sourcing.service.get_access_application",
        mock_repo.get_access_application,
    )
    try:
        reject_application("nonexistent-id", "reviewer1")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "不存在" in str(e)


def test_already_processed_approve_raises(monkeypatch):
    """approve_application raises ValueError when already approved."""
    mock_repo = MagicMock()
    mock_repo.get_access_application.return_value = {"status": "approved"}
    monkeypatch.setattr(
        "app.domains.sourcing.service.get_access_application",
        mock_repo.get_access_application,
    )
    try:
        approve_application("processed-id", "reviewer1")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "已处理" in str(e)


def test_category_filter_rejects_semantically_nearby_non_steel_suppliers() -> None:
    candidates = [
        {
            "supplier_name": "深圳市立创电子有限公司",
            "content": "深圳市立创电子有限公司 电子元器件 PCB 连接器",
            "metadata": {},
        },
        {
            "supplier_name": "钢材供应商有限公司",
            "content": "钢材供应商有限公司 钢板 型钢 碳钢",
            "metadata": {},
        },
    ]

    result = _filter_category_candidates(candidates, "钢材")

    assert [item["supplier_name"] for item in result] == ["钢材供应商有限公司"]


def test_category_filter_supports_steel_aliases() -> None:
    candidates = [
        {
            "supplier_name": "不锈钢供应商",
            "content": "不锈钢供应商 不锈钢板材",
            "metadata": {},
        }
    ]

    assert _filter_category_candidates(candidates, "钢材") == candidates


def test_category_filter_keeps_unstructured_search_backward_compatible() -> None:
    candidates = [{"supplier_name": "任意供应商", "content": "任意内容", "metadata": {}}]

    assert _filter_category_candidates(candidates, "") == candidates


def test_search_suppliers_returns_no_cross_category_results(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.domains.sourcing.service._search_feishu_snapshot_suppliers",
        lambda _request: None,
    )
    monkeypatch.setattr(
        "app.domains.sourcing.service.get_request",
        lambda _request_id: {"category": "钢材", "spec": ""},
    )
    monkeypatch.setattr("app.domains.sourcing.service.update_request_status", lambda *args: None)
    monkeypatch.setattr(
        "app.domains.sourcing_risk.discovery_service.search_external_provider",
        lambda *_args: [],
    )
    monkeypatch.setattr(
        "app.domains.sourcing.service._vector_search",
        lambda *_args, **_kwargs: [{
            "supplier_name": "电子供应商",
            "content": "电子供应商 电子元器件 PCB",
            "metadata": {},
            "match_score": 0.9,
        }],
    )

    def fail_if_risk_is_called(_candidates):
        raise AssertionError("跨品类候选不应进入风险评估")

    monkeypatch.setattr("app.domains.sourcing.service._batch_assess_risk", fail_if_risk_is_called)

    result = search_suppliers("request-1")

    assert result["results"] == []
    assert "钢材" in result["message"]


def test_search_suppliers_marks_local_results_for_typed_agent_routing(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.domains.sourcing.service._search_feishu_snapshot_suppliers",
        lambda _request: None,
    )
    monkeypatch.setattr(
        "app.domains.sourcing.service.get_request",
        lambda _request_id: {"category": "工业相机", "spec": ""},
    )
    monkeypatch.setattr("app.domains.sourcing.service.update_request_status", lambda *args: None)
    monkeypatch.setattr(
        "app.domains.sourcing.service._vector_search",
        lambda *_args, **_kwargs: [{
            "supplier_name": "深圳市康斯得电子有限公司",
            "content": "工业相机 视觉模组",
            "metadata": {},
            "match_score": 0.9,
        }],
    )
    monkeypatch.setattr(
        "app.domains.sourcing.service._filter_category_candidates",
        lambda candidates, _category: candidates,
    )
    monkeypatch.setattr(
        "app.domains.sourcing_risk.discovery_service.search_external_provider",
        lambda _requirement: [],
    )
    monkeypatch.setattr(
        "app.domains.sourcing.service._batch_assess_risk",
        lambda _candidates: {"深圳市康斯得电子有限公司": {"risk_score": 20, "risk_level": "low"}},
    )
    monkeypatch.setattr("app.domains.sourcing.service.save_result", lambda *_args: None)

    result = search_suppliers("request-1")

    assert result["results"][0]["candidate_type"] == "local"
    assert result["results"][0]["result_id"]


def test_search_suppliers_returns_gaishi_candidates_before_network_discovery(monkeypatch) -> None:
    monkeypatch.setattr("app.domains.sourcing.service.get_request", lambda _request_id: {"category": "制动系统", "spec": ""})
    monkeypatch.setattr("app.domains.sourcing.service.update_request_status", lambda *args: None)
    monkeypatch.setattr("app.domains.sourcing.service._search_feishu_snapshot_suppliers", lambda _request: None)
    monkeypatch.setattr("app.domains.sourcing.service._vector_search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "app.domains.sourcing.service._stage_gaishi_candidates",
        lambda _request, request_id: [{
            "candidate_id": "gasgoo-1",
            "supplier_name": "盖世制动候选",
            "source": "gasgoo_manual_export",
            "status": "staged_candidate",
            "verification_reasons": ["需通过天眼查核验企业主体与风险信息"],
        }] * 3,
    )
    monkeypatch.setattr("app.domains.sourcing.service.save_external_candidate", lambda *_args: None)

    result = search_suppliers("request-1")

    assert result["results"] == []
    assert len(result["external_candidates"]) == 3
    assert result["external_candidates"][0]["source"] == "gasgoo_manual_export"
    assert result["external_status"] == "gasgoo_manual_export"


def test_search_internal_history_candidates_uses_exact_material_number(monkeypatch) -> None:
    class Collection:
        def find(self, query):
            assert query == {"material_number": "23987432"}
            return self

        def limit(self, _limit):
            return [{
                "_id": "history:1", "supplier_code": "8110026", "supplier_name": "北京示例有限公司",
                "material_number": "23987432", "material_name": "自动变速器油", "base_code": "1000",
            }]

    monkeypatch.setattr("app.db.mongo.get_db", lambda: {"internal_supplier_material_relations": Collection()})

    candidates = _search_internal_history_candidates({"spec": "23987432"})

    assert candidates[0]["source_stage"] == "local_history"
    assert candidates[0]["supplier_code"] == "8110026"


def test_search_internal_history_candidates_groups_multiple_bases_by_supplier(monkeypatch) -> None:
    class Collection:
        def find(self, _query):
            return self

        def limit(self, _limit):
            return [
                {"_id": "history:1", "supplier_code": "8130047", "supplier_name": "示例制动企业", "material_number": "23748163", "material_name": "后轮制动鼓", "base_code": "1000"},
                {"_id": "history:2", "supplier_code": "8130047", "supplier_name": "示例制动企业", "material_number": "23748163", "material_name": "后轮制动鼓", "base_code": "3000"},
                {"_id": "history:3", "supplier_code": "8130048", "supplier_name": "另一制动企业", "material_number": "23748163", "material_name": "后轮制动鼓", "base_code": "1000"},
            ]

    monkeypatch.setattr("app.db.mongo.get_db", lambda: {"internal_supplier_material_relations": Collection()})

    candidates = _search_internal_history_candidates({"spec": "23748163"})

    assert [candidate["supplier_code"] for candidate in candidates] == ["8130047", "8130048"]
    assert "1000、3000" in candidates[0]["match_reasons"][1]


def test_search_suppliers_uses_feishu_snapshot_candidate_details(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.domains.sourcing.service.get_request",
        lambda _request_id: {"category": "工业相机", "spec": "4K"},
    )
    monkeypatch.setattr("app.domains.sourcing.service.update_request_status", lambda *args: None)
    monkeypatch.setattr(
        "app.domains.sourcing.service._search_feishu_snapshot_suppliers",
        lambda _request: [{
            "supplier_id": "feishu:supplier-1",
            "supplier_code": "SUP-001",
            "supplier_name": "工业相机供应商",
            "match_score": 1.0,
            "match_reasons": ["category:工业相机", "specification:4K"],
            "categories": ["工业相机"],
            "capabilities": [{"product_name": "4K工业相机模组"}],
            "contacts": [{"contact_name": "张三", "phone": "0755-12345678"}],
            "contact_person": "张三",
            "contact_phone": "0755-12345678",
            "source": "feishu_bitable",
        }],
    )
    monkeypatch.setattr(
        "app.domains.sourcing_risk.discovery_service.search_external_provider",
        lambda _requirement: [],
    )
    monkeypatch.setattr(
        "app.domains.sourcing.service._batch_assess_risk",
        lambda _candidates: {"工业相机供应商": {"risk_score": 20, "risk_level": "low"}},
    )
    monkeypatch.setattr("app.domains.sourcing.service.save_result", lambda *_args: None)

    result = search_suppliers("request-1")

    item = result["results"][0]
    assert item["supplier_id"] == "feishu:supplier-1"
    assert item["supplier_code"] == "SUP-001"
    assert item["capabilities"][0]["product_name"] == "4K工业相机模组"
    assert item["contact_phone"] == "0755-12345678"
    assert item["source"] == "feishu_bitable"


def test_external_candidate_requires_exact_identity_before_access(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.domains.sourcing.service.get_external_candidate",
        lambda _candidate_id: {
            "candidate_id": "candidate-1",
            "supplier_name": "网页候选",
            "identity_status": "ambiguous",
            "status": "staged_candidate",
        },
    )

    try:
        select_external_candidate("candidate-1", "apply_access", "agent")
        assert False, "未唯一核验的候选不应创建准入申请"
    except ValueError as exc:
        assert "不执行供应商准入" in str(exc)


def test_external_candidate_rejects_access_application_even_after_exact_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.domains.sourcing.service.get_external_candidate",
        lambda _candidate_id: {
            "candidate_id": "candidate-1",
            "supplier_name": "网页候选",
            "tianyancha_company_name": "天眼查确认企业",
            "identity_status": "exact",
            "status": "staged_candidate",
        },
    )
    try:
        select_external_candidate("candidate-1", "apply_access", "agent")
        assert False, "当前范围不应创建准入申请"
    except ValueError as exc:
        assert "不执行供应商准入" in str(exc)


def test_verify_external_candidate_marks_unavailable_when_tianyancha_fails(monkeypatch) -> None:
    candidate = {"_id": "candidate-1", "supplier_name": "待核验企业", "status": "staged_candidate"}
    updates = []
    monkeypatch.setattr("app.domains.sourcing.service.get_external_candidate", lambda _candidate_id: candidate)
    monkeypatch.setattr("app.db.mongo.get_db", lambda: {"external_supplier_candidates": type("Collection", (), {"update_one": lambda _self, *_args: updates.append(_args)})()})
    monkeypatch.setattr("app.services.tianyancha_client.fetch_company", lambda _name: False)

    result = verify_external_candidate("candidate-1")

    assert result == candidate
    assert updates


class FakeCollection:
    def update_one(self, *_args, **_kwargs):
        return None


def assert_candidate_payload(payload: dict) -> None:
    assert payload["supplier_name"] == "天眼查确认企业"
    assert payload["candidate_id"] == "candidate-1"
