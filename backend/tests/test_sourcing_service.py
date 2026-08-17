"""Tests for sourcing service boundaries and hard category gating."""

from unittest.mock import MagicMock

from app.domains.sourcing.service import (
    _filter_category_candidates,
    approve_application,
    reject_application,
    search_suppliers,
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
