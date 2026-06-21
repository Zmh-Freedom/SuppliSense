from unittest.mock import MagicMock, patch

from app.services.sourcing_service import (
    approve_application,
    reject_application,
)


def test_nonexistent_approve_raises(monkeypatch):
    """approve_application raises ValueError when app not found."""
    mock_repo = MagicMock()
    mock_repo.get_access_application.return_value = None
    monkeypatch.setattr(
        "app.services.sourcing_service.get_access_application",
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
        "app.services.sourcing_service.get_access_application",
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
        "app.services.sourcing_service.get_access_application",
        mock_repo.get_access_application,
    )
    try:
        approve_application("processed-id", "reviewer1")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "已处理" in str(e)
