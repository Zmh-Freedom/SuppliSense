from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.errors import DomainError


USER_ID = "00000000-0000-0000-0000-000000000011"
RUN_ID = "00000000-0000-4000-8000-000000000001"


def _task(source_type: str, source_id: str, updated_at: datetime) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "source_id": source_id,
        "title": f"{source_type}-{source_id}",
        "status": "COMPLETED",
        "updated_at": updated_at.isoformat(),
        "time_basis": "updated_at",
        "resume_url": f"/sourcing?source={source_type}&id={source_id}",
    }


def _install_workspace_rows(monkeypatch: pytest.MonkeyPatch, run_rows: list[dict], legacy_rows: list[dict]) -> None:
    from app.domains.sourcing import workspace_service

    def after_cursor(row: dict, cursor: dict | None) -> bool:
        if cursor is None:
            return True
        current_time = datetime.fromisoformat(row["updated_at"])
        current = (current_time, row["source_type"], row["source_id"])
        cursor_time = datetime.fromisoformat(cursor["updated_at"])
        cursor_key = (cursor_time, cursor["source_type"], cursor["source_id"])
        return current[0] < cursor_key[0] or (
            current[0] == cursor_key[0] and current[1:] > cursor_key[1:]
        )

    def rows(items: list[dict], *, cursor: str | None, limit: int, **_kwargs: Any) -> list[dict]:
        return sorted(
            [row for row in items if after_cursor(row, cursor)],
            key=lambda row: (-datetime.fromisoformat(row["updated_at"]).timestamp(), row["source_type"], row["source_id"]),
        )[:limit]

    monkeypatch.setattr(workspace_service, "list_sourcing_risk_runs_with_cursor", lambda **kwargs: rows(run_rows, **kwargs))
    monkeypatch.setattr(workspace_service, "list_requests_with_cursor", lambda **kwargs: rows(legacy_rows, **kwargs))
    monkeypatch.setattr(
        workspace_service,
        "get_rollout_state_snapshot",
        lambda: {"state": "active", "stage": "default"},
    )
    monkeypatch.setattr(workspace_service, "agent_run_v2_route", lambda *_args, **_kwargs: "v2")


def test_list_sourcing_tasks_paginates_same_timestamp_and_keeps_colliding_ids_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.sourcing.workspace_service import list_sourcing_tasks

    same_time = datetime(2026, 10, 23, 8, tzinfo=timezone.utc)
    runs = [_task("agent_run", "run-b", same_time), _task("agent_run", "shared", same_time)]
    legacy = [_task("legacy_request", "shared", same_time), _task("legacy_request", "legacy-z", same_time)]
    _install_workspace_rows(monkeypatch, runs, legacy)

    first = list_sourcing_tasks(USER_ID, "purchaser", cursor=None, limit=2)
    second = list_sourcing_tasks(USER_ID, "purchaser", cursor=first["next_cursor"], limit=2)

    keys = [(item["source_type"], item["source_id"]) for item in [*first["items"], *second["items"]]]
    assert keys == [
        ("agent_run", "run-b"),
        ("agent_run", "shared"),
        ("legacy_request", "legacy-z"),
        ("legacy_request", "shared"),
    ]
    assert len(keys) == len(set(keys))
    assert first["next_cursor"] is not None
    assert second["next_cursor"] is None


def test_list_sourcing_tasks_keeps_manager_scope_private_and_rejects_global_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.sourcing import workspace_service

    calls: list[tuple[str | None, bool]] = []
    monkeypatch.setattr(workspace_service, "get_rollout_state_snapshot", lambda: {"state": "active", "stage": "default"})
    monkeypatch.setattr(workspace_service, "agent_run_v2_route", lambda *_args, **_kwargs: "v2")
    monkeypatch.setattr(workspace_service, "list_sourcing_risk_runs_with_cursor", lambda **kwargs: calls.append((kwargs["user_id"], kwargs["scope_all"])) or [])
    monkeypatch.setattr(workspace_service, "list_requests_with_cursor", lambda **_kwargs: [])

    workspace_service.list_sourcing_tasks(USER_ID, "manager", cursor=None, limit=20)
    assert calls == [(USER_ID, False)]

    with pytest.raises(DomainError) as error:
        workspace_service.list_sourcing_tasks(USER_ID, "manager", cursor=None, limit=20, scope="all")
    assert error.value.status_code == 403
    assert calls == [(USER_ID, False)]


def test_list_sourcing_tasks_disables_run_source_outside_rollout_without_leaking_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    from app.domains.sourcing import workspace_service

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", False)
    monkeypatch.setattr(workspace_service, "get_rollout_state_snapshot", lambda: {"state": "active", "stage": "default"})
    monkeypatch.setattr(workspace_service, "list_sourcing_risk_runs_with_cursor", lambda **_kwargs: pytest.fail("disabled run source must not be queried"))
    legacy_row = _task("legacy_request", "legacy-1", datetime(2026, 10, 22, tzinfo=timezone.utc))
    monkeypatch.setattr(workspace_service, "list_requests_with_cursor", lambda **_kwargs: [legacy_row])

    result = workspace_service.list_sourcing_tasks(USER_ID, "purchaser", cursor=None, limit=20)

    assert [item["source_id"] for item in result["items"]] == ["legacy-1"]
    assert result["sources"]["agent_run"]["status"] == "disabled"
    assert result["sources"]["legacy_request"]["status"] == "available"


def test_list_sourcing_tasks_marks_partial_source_failure_and_requires_page_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.sourcing import workspace_service

    run_row = _task("agent_run", RUN_ID, datetime(2026, 10, 23, tzinfo=timezone.utc))
    monkeypatch.setattr(workspace_service, "get_rollout_state_snapshot", lambda: {"state": "active", "stage": "default"})
    monkeypatch.setattr(workspace_service, "list_sourcing_risk_runs_with_cursor", lambda **_kwargs: [run_row])
    monkeypatch.setattr(workspace_service, "list_requests_with_cursor", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("mongo unavailable")))

    result = workspace_service.list_sourcing_tasks(USER_ID, "purchaser", cursor=None, limit=1)

    assert result["items"] == [run_row]
    assert result["next_cursor"] is None
    assert result["sources"]["legacy_request"]["status"] == "unavailable"
    assert "mongo unavailable" not in str(result)


def test_list_sourcing_tasks_does_not_page_when_rollout_control_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.sourcing import workspace_service

    monkeypatch.setattr(workspace_service, "get_rollout_state_snapshot", lambda: None)
    monkeypatch.setattr(workspace_service, "list_sourcing_risk_runs_with_cursor", lambda **_kwargs: pytest.fail("unavailable rollout control must not query runs"))
    rows = [
        _task("legacy_request", "legacy-1", datetime(2026, 10, 23, tzinfo=timezone.utc)),
        _task("legacy_request", "legacy-2", datetime(2026, 10, 22, tzinfo=timezone.utc)),
    ]
    monkeypatch.setattr(workspace_service, "list_requests_with_cursor", lambda **_kwargs: rows)

    result = workspace_service.list_sourcing_tasks(USER_ID, "purchaser", cursor=None, limit=1)

    assert result["sources"]["agent_run"]["status"] == "unavailable"
    assert result["next_cursor"] is None


def test_list_sourcing_tasks_rejects_malformed_cursor() -> None:
    from app.domains.sourcing.workspace_service import list_sourcing_tasks

    with pytest.raises(DomainError) as error:
        list_sourcing_tasks(USER_ID, "purchaser", cursor="not-a-cursor", limit=20)
    assert error.value.status_code == 400


def test_run_history_repository_filters_owner_and_source_type_before_paging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    from app.domains.agent_run import repo

    class Cursor:
        description = [("id",), ("run_type",), ("status",), ("requirement",), ("created_at",), ("updated_at",)]

        def __init__(self) -> None:
            self.query = ""
            self.params: tuple[Any, ...] = ()

        def execute(self, query: str, params: tuple[Any, ...]) -> None:
            self.query, self.params = query, params

        def fetchall(self) -> list[tuple]:
            return [(RUN_ID, "sourcing_risk_v2", "COMPLETED", {"requirement_text": "摄像头"}, datetime.now(timezone.utc), datetime.now(timezone.utc))]

    cursor = Cursor()

    @contextmanager
    def fake_get_cursor():
        yield None, cursor

    monkeypatch.setattr(repo, "get_cursor", fake_get_cursor)

    rows = repo.list_sourcing_risk_runs_with_cursor(
        user_id=USER_ID,
        scope_all=False,
        cursor=None,
        limit=5,
    )

    assert rows[0]["source_type"] == "agent_run"
    assert "run_type = %s" in cursor.query
    assert "user_id = %s" in cursor.query
    assert "agent_harness" not in cursor.query
    assert "sourcing_risk_v2" in cursor.params
    assert USER_ID in cursor.params


def test_legacy_request_detail_is_scoped_to_owner_or_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.sourcing import repo, service

    request = {
        "_id": "legacy-1",
        "user_id": "another-user",
        "title": "受限请求",
        "category": "电子元件",
        "spec": "规格",
        "status": "done",
        "created_at": datetime.now(timezone.utc),
    }
    monkeypatch.setattr(service, "get_request", lambda _request_id: request)
    monkeypatch.setattr(service, "get_results", lambda _request_id: [])

    assert service.get_sourcing_request_detail_for_user("legacy-1", USER_ID, "purchaser") is None
    assert service.get_sourcing_request_detail_for_user("legacy-1", USER_ID, "admin")["request_id"] == "legacy-1"


def test_legacy_search_stream_rejects_requests_owned_by_another_user(
    sourcing_workspace_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.sourcing import service

    monkeypatch.setattr(service, "get_sourcing_request_detail_for_user", lambda *_args: None)
    monkeypatch.setattr(service, "search_suppliers", lambda *_args: pytest.fail("unauthorized search must not start"))

    response = sourcing_workspace_client.post("/api/v1/sourcing/requests/another-users-request/search")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "寻源请求不存在"


@pytest.fixture
def sourcing_workspace_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from app.core.security import create_access_token
    from app.main import app
    from app.schemas.user import UserInDB

    @asynccontextmanager
    async def no_lifespan(_app):
        yield

    user = UserInDB(
        id=USER_ID,
        username="sourcing-workspace-user",
        email="sourcing-workspace@example.com",
        role="purchaser",
        password_hash="unused",
        created_at=datetime.now(timezone.utc),
        is_active=True,
    )
    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)
    monkeypatch.setattr("app.core.deps.get_user_by_id", lambda _user_id: user)
    with TestClient(app) as client:
        client.headers.update({"Authorization": f"Bearer {create_access_token({'sub': USER_ID})}"})
        yield client


def test_sourcing_workspace_tasks_api_exposes_separate_source_items(
    sourcing_workspace_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.sourcing import api_workspace

    monkeypatch.setattr(
        api_workspace,
        "list_sourcing_tasks",
        lambda user_id, user_role, **kwargs: {
            "items": [_task("agent_run", "same", datetime(2026, 10, 23, tzinfo=timezone.utc))],
            "next_cursor": None,
            "sources": {
                "agent_run": {"status": "available"},
                "legacy_request": {"status": "unavailable", "reason": "暂不可用"},
            },
        },
    )

    response = sourcing_workspace_client.get("/api/v1/sourcing-workspace/tasks?limit=5")

    assert response.status_code == 200
    assert response.json()["items"][0]["source_type"] == "agent_run"
    assert response.json()["sources"]["legacy_request"]["status"] == "unavailable"


def test_sourcing_workspace_tasks_api_rejects_an_invalid_cursor_before_reading_sources(
    sourcing_workspace_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.sourcing import workspace_service

    monkeypatch.setattr(workspace_service, "list_sourcing_risk_runs_with_cursor", lambda **_kwargs: pytest.fail("invalid cursor must be rejected before source reads"))
    monkeypatch.setattr(workspace_service, "list_requests_with_cursor", lambda **_kwargs: pytest.fail("invalid cursor must be rejected before source reads"))

    response = sourcing_workspace_client.get("/api/v1/sourcing-workspace/tasks?cursor=invalid")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SOURCING_WORKSPACE_CURSOR_INVALID"
