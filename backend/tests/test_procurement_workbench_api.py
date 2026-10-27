from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.domains.alert import api_workbench, workbench_service
from app.schemas.user import UserInDB, UserRole


def _user(role: UserRole = UserRole.PURCHASER) -> UserInDB:
    return UserInDB(
        id="buyer-1",
        username="buyer",
        email="buyer@example.com",
        role=role,
        password_hash="unused",
        created_at=datetime.now(timezone.utc),
        is_active=True,
    )


def test_workbench_route_forwards_scope_cursor_and_limit(monkeypatch):
    captured = {}

    def fake_workbench(user_id, user_role, **kwargs):
        captured.update(user_id=user_id, user_role=user_role, **kwargs)
        return {"scope": {"view": "department"}}

    monkeypatch.setattr(workbench_service, "get_procurement_workbench", fake_workbench)

    result = asyncio.run(api_workbench.get_procurement_workbench_endpoint(
        view="department",
        department_code="D1",
        cursor="opaque",
        limit=5,
        current_user=_user(),
    ))

    assert result["scope"]["view"] == "department"
    assert captured == {
        "user_id": "buyer-1",
        "user_role": "purchaser",
        "view": "department",
        "department_code": "D1",
        "cursor": "opaque",
        "limit": 5,
    }


def test_workbench_route_maps_scope_denial(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        workbench_service,
        "get_procurement_workbench",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("当前管理科室无权访问")),
    )

    try:
        asyncio.run(api_workbench.get_procurement_workbench_endpoint(
            view="department", current_user=_user()
        ))
    except HTTPException as error:
        assert error.status_code == 403
    else:
        raise AssertionError("scope denial must map to HTTP 403")


def test_workbench_route_is_registered():
    assert any(
        route.path == "/procurement-workbench"
        for route in api_workbench.router.routes
    )
