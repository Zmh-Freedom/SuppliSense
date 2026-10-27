"""Initial admin bootstrap must not accept a published template secret."""

import pytest

from app import main


def test_validate_config_rejects_placeholder_admin_password(monkeypatch) -> None:
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "CHANGE_ME_设置一个强密码")

    with pytest.raises(SystemExit):
        main._validate_config()


def test_first_start_requires_admin_password_when_no_users(monkeypatch) -> None:
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr("app.domains.auth.service.list_users", lambda: [])

    with pytest.raises(SystemExit):
        main.create_default_admin()
