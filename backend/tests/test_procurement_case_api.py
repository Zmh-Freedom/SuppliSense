from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.domains.alert import api_cases
from app.domains.alert import case_service
from app.schemas.user import UserInDB, UserRole
from datetime import datetime, timezone


def _user() -> UserInDB:
    return UserInDB(
        id="buyer-1",
        username="buyer",
        email="buyer@example.com",
        role=UserRole.PURCHASER,
        password_hash="unused",
        created_at=datetime.now(timezone.utc),
        is_active=True,
    )


def test_case_command_schema_rejects_fields_for_other_commands():
    request = getattr(api_cases, "CaseCommandRequest", None)
    assert request is not None, "CaseCommandRequest must be implemented"

    with pytest.raises(ValueError):
        request.model_validate({"command": "claim", "expected_version": 1, "owner_user_id": "buyer-2"})


def test_case_detail_uses_404_for_inaccessible_cases(monkeypatch: pytest.MonkeyPatch):
    endpoint = getattr(api_cases, "get_procurement_case", None)
    assert callable(endpoint), "get_procurement_case must be implemented"
    monkeypatch.setattr(case_service, "get_case_detail", lambda *_args: None)

    with pytest.raises(HTTPException) as error:
        asyncio.run(endpoint("hidden-case", _user()))

    assert error.value.status_code == 404


def test_idempotency_conflicts_map_to_http_409(monkeypatch: pytest.MonkeyPatch):
    endpoint = getattr(api_cases, "send_procurement_case_command", None)
    assert callable(endpoint), "send_procurement_case_command must be implemented"
    conflict = getattr(case_service, "CaseConflictError", ValueError)

    def raise_conflict(*_args):
        raise conflict("事项版本已变化")

    monkeypatch.setattr(case_service, "handle_case_command", raise_conflict)

    with pytest.raises(HTTPException) as error:
        asyncio.run(endpoint("case-1", {"command": "claim", "expected_version": 1}, "key-1", _user()))

    assert error.value.status_code == 409


def test_missing_case_maps_to_http_404(monkeypatch: pytest.MonkeyPatch):
    endpoint = api_cases.send_procurement_case_command

    def raise_missing(*_args):
        raise case_service.CaseNotFoundError("采购事项不存在")

    monkeypatch.setattr(case_service, "handle_case_command", raise_missing)
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            endpoint(
                "missing-case",
                {"command": "claim", "expected_version": 1},
                "key-1",
                _user(),
            )
        )

    assert error.value.status_code == 404


def test_procurement_case_routes_are_registered_under_the_new_prefix():
    routes = {route.path for route in api_cases.router.routes}

    assert "/procurement-cases" in routes
    assert "/procurement-cases/{case_id}" in routes
    assert "/procurement-cases/{case_id}/commands" in routes
    assert "/procurement-cases/{case_id}/review-tasks" in routes
