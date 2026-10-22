"""Owner/service-principal mapping and idempotent watchlist migration."""

from datetime import datetime, timezone

from app.domains.scheduling import migration, repo

OWNER = "00000000-0000-4000-8000-000000000101"
OTHER = "00000000-0000-4000-8000-000000000102"
SERVICE = "scheduler:watchlist-monitor"


def _targets():
    return [
        {"monitor_target_id": "t-owner", "owner_user_id": OWNER, "supplier_id": "s-1"},
        {"monitor_target_id": "t-inactive", "owner_user_id": OTHER},
        {"monitor_target_id": "t-none"},
        {"monitor_target_id": "t-denied", "owner_user_id": OWNER},
    ]


def _setup(monkeypatch, existing=None):
    monkeypatch.setattr(migration.settings, "SCHEDULER_SERVICE_PRINCIPALS", SERVICE)
    monkeypatch.setattr(
        "app.domains.alert.service.get_watchlist_targets",
        lambda user_id=None, role=None: (
            _targets() if user_id is None else [t for t in _targets() if t["monitor_target_id"] == "t-owner"]
        ),
    )
    monkeypatch.setattr(migration, "_active_users", lambda ids: {OWNER: "purchaser"})
    monkeypatch.setattr(migration.repo, "list_schedules", lambda **kwargs: existing or [])


def test_mapping_prefers_an_accessible_owner_then_explicit_service_principal(monkeypatch):
    _setup(monkeypatch)
    plan = migration.build_watchlist_migration(
        service_principal=SERVICE, now=datetime(2026, 10, 22, tzinfo=timezone.utc)
    )
    by_target = {entry["target_id"]: entry for entry in plan["entries"]}
    assert by_target["t-owner"]["mapping"] == "target_owner"
    assert (by_target["t-owner"]["actor_kind"], by_target["t-owner"]["actor_id"]) == ("user", OWNER)
    assert by_target["t-inactive"]["mapping"] == "service_principal_owner_inactive"
    assert by_target["t-none"]["mapping"] == "service_principal_no_owner"
    assert by_target["t-denied"]["mapping"] == "service_principal_owner_access_denied"
    assert all(entry["actor_kind"] == "service" for key, entry in by_target.items() if key != "t-owner")
    assert plan["counts"] == {"create": 4, "update": 0, "noop": 0, "skip": 0}


def test_without_a_service_principal_unmapped_targets_are_reported_not_guessed(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(migration.settings, "SCHEDULER_SERVICE_PRINCIPALS", "")
    plan = migration.build_watchlist_migration(
        service_principal="", now=datetime(2026, 10, 22, tzinfo=timezone.utc)
    )
    by_target = {entry["target_id"]: entry for entry in plan["entries"]}
    assert by_target["t-owner"]["action"] == "create"
    assert by_target["t-none"]["action"] == "skip"
    assert by_target["t-none"]["reason"] == "no_eligible_owner_or_service_principal"
    assert plan["counts"] == {"create": 1, "update": 0, "noop": 0, "skip": 3}


def test_identical_definition_is_noop_and_mapping_change_bumps_revision(monkeypatch):
    _setup(monkeypatch)
    first = migration.build_watchlist_migration(
        service_principal=SERVICE, now=datetime(2026, 10, 22, tzinfo=timezone.utc)
    )
    owner_entry = next(entry for entry in first["entries"] if entry["target_id"] == "t-owner")
    existing = [{
        "schedule_id": owner_entry["schedule_id"],
        "revision": 1,
        "target_id": "t-owner",
        "cron": owner_entry["definition"]["cron"],
        "timezone": owner_entry["definition"]["timezone"],
        "actor_kind": "user",
        "actor_id": OWNER,
        "kind": "financial_check",
        "scope": owner_entry["definition"]["scope"],
    }]
    _setup(monkeypatch, existing=existing)
    same = migration.build_watchlist_migration(
        service_principal=SERVICE, now=datetime(2026, 10, 23, tzinfo=timezone.utc)
    )
    assert next(e for e in same["entries"] if e["target_id"] == "t-owner")["action"] == "noop"

    existing[0]["actor_kind"] = "service"
    existing[0]["actor_id"] = SERVICE
    changed = migration.build_watchlist_migration(
        service_principal=SERVICE, now=datetime(2026, 10, 23, tzinfo=timezone.utc)
    )
    entry = next(e for e in changed["entries"] if e["target_id"] == "t-owner")
    assert entry["action"] == "update"
    assert entry["revision"] == 2
    assert entry["definition"]["revision"] == 2


def test_apply_only_writes_create_and_update(monkeypatch):
    _setup(monkeypatch)
    written = []
    monkeypatch.setattr(
        migration.repo, "register_schedule",
        lambda definition: written.append((definition.schedule_id, definition.revision))
        or {"created": True},
    )
    plan = migration.build_watchlist_migration(
        service_principal=SERVICE, now=datetime(2026, 10, 22, tzinfo=timezone.utc)
    )
    result = migration.apply_watchlist_migration(plan)
    assert result["count"] == 4
    assert len(written) == 4


def test_service_principal_must_be_explicitly_allowed(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(migration.settings, "SCHEDULER_SERVICE_PRINCIPALS", "")
    import pytest
    with pytest.raises(ValueError, match="SCHEDULER_SERVICE_PRINCIPALS"):
        migration.build_watchlist_migration(service_principal=SERVICE)
