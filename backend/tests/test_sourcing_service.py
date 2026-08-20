"""Tests for sourcing service boundaries and hard category gating."""

from unittest.mock import MagicMock

from app.domains.sourcing.service import (
    _filter_category_candidates,
    approve_application,
    reject_application,
    search_suppliers,
    select_external_candidate,
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
        assert "唯一身份核验" in str(exc)


def test_external_candidate_creates_idempotent_access_application_after_exact_identity(monkeypatch) -> None:
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
    monkeypatch.setattr(
        "app.domains.sourcing.service.get_access_application_by_candidate",
        lambda _candidate_id: None,
    )
    monkeypatch.setattr(
        "app.domains.sourcing.service.create_access_application",
        lambda **kwargs: (assert_candidate_payload(kwargs) or "application-1"),
    )
    monkeypatch.setattr(
        "app.db.mongo.get_db",
        lambda: {"external_supplier_candidates": FakeCollection()},
    )

    result = select_external_candidate("candidate-1", "apply_access", "agent")

    assert result == {
        "success": True,
        "action": "apply_access",
        "application_id": "application-1",
        "candidate_id": "candidate-1",
    }


class FakeCollection:
    def update_one(self, *_args, **_kwargs):
        return None


def assert_candidate_payload(payload: dict) -> None:
    assert payload["supplier_name"] == "天眼查确认企业"
    assert payload["candidate_id"] == "candidate-1"
