"""Audit persistence failures must be visible to operators."""

from unittest.mock import MagicMock

import pytest

from app.domains.auth import audit


def test_log_action_reports_persistence_failure(monkeypatch) -> None:
    logger = MagicMock()
    monkeypatch.setattr(audit.settings, "USE_PG_USERS", True)
    monkeypatch.setattr(audit, "logger", logger)
    monkeypatch.setattr(
        audit,
        "create_log",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("postgres unavailable")),
    )

    audit.log_action("auth.login", user_id="user-1")

    logger.error.assert_called_once()


def test_required_audit_stops_sensitive_write_when_persistence_fails(monkeypatch) -> None:
    logger = MagicMock()
    monkeypatch.setattr(audit.settings, "USE_PG_USERS", True)
    monkeypatch.setattr(audit, "logger", logger)
    monkeypatch.setattr(
        audit,
        "create_log",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("postgres unavailable")),
    )

    with pytest.raises(audit.AuditUnavailable):
        audit.log_action("alert.paid_refresh_requested", user_id="user-1", required=True)

    logger.error.assert_called_once()
