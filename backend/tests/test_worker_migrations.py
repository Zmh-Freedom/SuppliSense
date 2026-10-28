"""Versioned schema prerequisites for independent worker roles."""

import asyncio
import importlib
import sys
from contextlib import contextmanager
from inspect import signature
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.db import init_pg, mongo, worker_migrations


def test_worker_migration_api_is_available() -> None:
    assert callable(getattr(worker_migrations, "apply_worker_migrations", None))
    assert callable(getattr(worker_migrations, "worker_migration_status", None))
    assert callable(getattr(worker_migrations, "assert_worker_schema_ready", None))


def test_postgres_worker_migration_includes_checkpointer_setup(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(init_pg, "ensure_pg_schema", lambda **kwargs: calls.append(("app", kwargs)))
    monkeypatch.setattr(
        worker_migrations,
        "_apply_checkpointer_schema",
        lambda: calls.append(("checkpointer", {})),
        raising=False,
    )

    worker_migrations._apply_postgres_schema()

    assert calls == [
        ("app", {"migrate_legacy_roles": False}),
        ("checkpointer", {}),
    ]


def _migration_fakes(monkeypatch):
    records: dict[str, dict] = {}
    calls = {"postgres": 0, "mongo": 0}

    def read_migration(store: str, version: str) -> dict | None:
        del version
        return records.get(store)

    def record_migration(store: str, version: str, checksum: str) -> None:
        records[store] = {"version": version, "checksum": checksum}

    def apply_postgres() -> None:
        calls["postgres"] += 1

    def apply_mongo() -> None:
        calls["mongo"] += 1

    monkeypatch.setattr(settings, "PG_DB", "worker_preprod_stage3_test")
    monkeypatch.setattr(settings, "MONGO_DB", "worker_preprod_stage3_test")
    monkeypatch.setattr(worker_migrations, "_ensure_postgres_history_table", lambda: None)
    monkeypatch.setattr(worker_migrations, "_read_migration", read_migration, raising=False)
    monkeypatch.setattr(
        worker_migrations,
        "_list_migration_versions",
        lambda store: [records[store]["version"]] if store in records else [],
        raising=False,
    )
    monkeypatch.setattr(worker_migrations, "_record_migration", record_migration, raising=False)
    monkeypatch.setattr(worker_migrations, "_apply_postgres_schema", apply_postgres, raising=False)
    monkeypatch.setattr(worker_migrations, "_apply_mongo_indexes", apply_mongo, raising=False)
    return records, calls


def test_worker_migration_refuses_shared_development_databases(monkeypatch) -> None:
    ensure_history = []
    monkeypatch.setattr(settings, "PG_DB", "sra")
    monkeypatch.setattr(settings, "MONGO_DB", "tianyancha")
    monkeypatch.setattr(
        worker_migrations,
        "_ensure_postgres_history_table",
        lambda: ensure_history.append(True),
    )

    with pytest.raises(RuntimeError, match="拒绝在非隔离数据库"):
        worker_migrations.apply_worker_migrations()

    assert ensure_history == []


def test_worker_migrations_record_both_stores_and_are_idempotent(monkeypatch) -> None:
    _, calls = _migration_fakes(monkeypatch)

    first = worker_migrations.apply_worker_migrations()
    second = worker_migrations.apply_worker_migrations()

    assert first["ready"] is True
    assert second["ready"] is True
    assert first["version"] == worker_migrations.CURRENT_WORKER_SCHEMA_VERSION
    assert calls == {"postgres": 1, "mongo": 1}
    assert worker_migrations.worker_migration_status()["ready"] is True
    worker_migrations.assert_worker_schema_ready()


def test_mongo_migration_failure_keeps_workers_blocked_and_retry_reuses_postgres(monkeypatch) -> None:
    records, calls = _migration_fakes(monkeypatch)
    fail_mongo = True

    def apply_mongo() -> None:
        nonlocal fail_mongo
        calls["mongo"] += 1
        if fail_mongo:
            raise RuntimeError("index creation failed")

    monkeypatch.setattr(worker_migrations, "_apply_mongo_indexes", apply_mongo)

    with pytest.raises(RuntimeError, match="index creation failed"):
        worker_migrations.apply_worker_migrations()
    assert records["postgres"]["version"] == worker_migrations.CURRENT_WORKER_SCHEMA_VERSION
    assert "mongo" not in records
    assert worker_migrations.worker_migration_status()["ready"] is False
    with pytest.raises(RuntimeError):
        worker_migrations.assert_worker_schema_ready()

    fail_mongo = False
    result = worker_migrations.apply_worker_migrations()

    assert result["ready"] is True
    assert calls == {"postgres": 1, "mongo": 2}


def test_checksum_drift_blocks_worker_without_reapplying_schema(monkeypatch) -> None:
    records, calls = _migration_fakes(monkeypatch)
    records["postgres"] = {
        "version": worker_migrations.CURRENT_WORKER_SCHEMA_VERSION,
        "checksum": "older-checksum",
    }

    with pytest.raises(RuntimeError, match="checksum"):
        worker_migrations.apply_worker_migrations()

    assert calls == {"postgres": 0, "mongo": 0}
    assert worker_migrations.worker_migration_status()["ready"] is False


def test_unknown_newer_schema_version_blocks_workers_and_migration(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PG_DB", "worker_preprod_stage3_test")
    monkeypatch.setattr(settings, "MONGO_DB", "worker_preprod_stage3_test")
    history_table = []
    monkeypatch.setattr(
        worker_migrations,
        "_ensure_postgres_history_table",
        lambda: history_table.append(True),
    )
    monkeypatch.setattr(
        worker_migrations,
        "_list_migration_versions",
        lambda store: ["worker-runtime-999"] if store == "postgres" else [],
        raising=False,
    )
    monkeypatch.setattr(
        worker_migrations,
        "_read_migration",
        lambda store, version: {
            "version": version,
            "checksum": worker_migrations._expected_checksum(
                store, worker_migrations.WORKER_SCHEMA_MIGRATIONS[0]
            ),
        },
    )

    status = worker_migrations.worker_migration_status()

    assert status["ready"] is False
    assert status["stores"]["postgres"]["status"] == "version_mismatch"
    with pytest.raises(RuntimeError, match="unsupported worker schema versions"):
        worker_migrations.apply_worker_migrations()
    assert history_table == []


def test_worker_schema_migration_can_skip_legacy_user_data_rewrite() -> None:
    assert "migrate_legacy_roles" in signature(init_pg.ensure_pg_schema).parameters


@pytest.mark.parametrize("migrate_legacy_roles", [False, True])
def test_postgres_schema_initializer_controls_legacy_user_rewrite(
    monkeypatch, migrate_legacy_roles: bool,
) -> None:
    statements: list[str] = []

    class FakeCursor:
        def execute(self, statement: str) -> None:
            statements.append(statement)

    @contextmanager
    def fake_cursor():
        yield object(), FakeCursor()

    monkeypatch.setattr(init_pg, "get_cursor", fake_cursor)
    monkeypatch.setattr(init_pg, "DDL_STATEMENTS", [])
    monkeypatch.setattr(init_pg, "INDEX_STATEMENTS", [])
    monkeypatch.setattr(init_pg, "_ensure_verified_evidence_constraint", lambda cursor: None)
    monkeypatch.setattr(init_pg, "_ensure_agent_run_constraints", lambda cursor: None)
    monkeypatch.setattr("app.db.assessment_schema.ASSESSMENT_DDL", [])

    init_pg.ensure_pg_schema(migrate_legacy_roles=migrate_legacy_roles)

    assert any("UPDATE users SET role = 'purchaser'" in item for item in statements) is migrate_legacy_roles


def test_mongo_index_migration_supports_strict_failure_mode() -> None:
    assert "strict" in signature(mongo.ensure_indexes).parameters


def test_strict_mongo_index_migration_propagates_individual_index_failure(monkeypatch) -> None:
    class IndexCollection:
        def __init__(self, name: str) -> None:
            self.name = name

        def index_information(self) -> dict:
            return {}

        def create_index(self, *args, **kwargs) -> None:
            del args, kwargs
            if self.name == "riskInfo":
                raise OSError("index build failed")

    class FakeDatabase:
        def __getitem__(self, name: str) -> IndexCollection:
            return IndexCollection(name)

    monkeypatch.setattr(mongo, "get_db", lambda: FakeDatabase())

    with pytest.raises(RuntimeError, match="index migration failed"):
        mongo.ensure_indexes(strict=True)


@pytest.mark.parametrize(
    ("module_name", "runner_name"),
    [
        ("app.workers.agent_worker", "run_worker_once"),
        ("app.workers.scheduler", "run_scheduler_once"),
        ("app.workers.scheduled_worker", "run_scheduled_worker_once"),
        ("app.workers.delivery", "run_delivery_once"),
    ],
)
def test_worker_once_entrypoints_fail_before_claiming_when_schema_is_unready(
    monkeypatch, module_name: str, runner_name: str,
) -> None:
    module = importlib.import_module(module_name)

    def reject_unready_schema() -> None:
        raise RuntimeError("worker schema gate")

    def claim_work(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("worker claimed work before migration check")

    monkeypatch.setattr(module, "assert_worker_schema_ready", reject_unready_schema, raising=False)
    monkeypatch.setattr(module, runner_name, claim_work)
    monkeypatch.setattr(sys, "argv", [module_name, "--once"])
    if module_name == "app.workers.scheduler":
        monkeypatch.setattr(module, "_assert_role_available", lambda: None)
    if module_name in {"app.workers.scheduler", "app.workers.scheduled_worker"}:
        monkeypatch.setattr(module.settings, "SCHEDULER_ROLE_ENABLED", True)
        monkeypatch.setattr(module.settings, "SCHEDULER_MIGRATED_KINDS", "financial_check")
        monkeypatch.setattr(module.settings, "SCHEDULER_PAID_EXECUTION_ENABLED", False)
    if module_name == "app.workers.delivery":
        monkeypatch.setattr(module.settings, "DELIVERY_ROLE_ENABLED", False)
        monkeypatch.setattr(module.settings, "OUTBOX_DELIVERY_SINK", "test")
    if module_name == "app.workers.agent_worker":
        monkeypatch.setattr(module.settings, "AGENT_EXECUTION_MODE", "worker")

    with pytest.raises(RuntimeError, match="worker schema gate"):
        module.main()


def test_agent_worker_opens_migrated_checkpointer_without_setup(monkeypatch) -> None:
    from app.graphs.sourcing_risk_v2 import checkpointer
    from app.workers import agent_worker

    saver = object()
    getter = AsyncMock(return_value=saver)
    monkeypatch.setattr(checkpointer, "get_sourcing_risk_checkpointer", getter)

    result = asyncio.run(agent_worker._get_checkpointer())

    assert result is saver
    getter.assert_awaited_once_with(setup_schema=False)


def test_checkpointer_accessor_can_skip_all_schema_mutations(monkeypatch) -> None:
    from app.graphs.sourcing_risk_v2 import checkpointer

    fake = AsyncMock()
    monkeypatch.setattr(checkpointer, "_new_checkpointer", AsyncMock(return_value=fake))

    result = asyncio.run(
        checkpointer.get_sourcing_risk_checkpointer(setup_schema=False)
    )

    assert result is fake
    fake.conn.execute.assert_not_awaited()
    fake.setup.assert_not_awaited()


def test_worker_role_config_rejects_agent_worker_when_api_is_inline(monkeypatch) -> None:
    from app.workers import role_config

    monkeypatch.setattr(settings, "AGENT_EXECUTION_MODE", "inline")

    with pytest.raises(RuntimeError, match="AGENT_EXECUTION_MODE=worker"):
        role_config.assert_agent_worker_enabled()


@pytest.mark.parametrize(
    ("migrated_kinds", "paid_enabled", "message"),
    [
        ("financial_check", True, "预生产阶段必须关闭调度付费执行"),
        ("financial_check,full_refresh", False, "预生产阶段不允许迁移 full_refresh"),
        ("financial_check,daily_digest", False, "只允许已验证的 financial_check"),
    ],
)
def test_worker_role_config_keeps_paid_refresh_fail_closed(
    monkeypatch, migrated_kinds: str, paid_enabled: bool, message: str,
) -> None:
    from app.workers import role_config

    monkeypatch.setattr(settings, "SCHEDULER_MIGRATED_KINDS", migrated_kinds)
    monkeypatch.setattr(settings, "SCHEDULER_PAID_EXECUTION_ENABLED", paid_enabled)

    with pytest.raises(RuntimeError, match=message):
        role_config.assert_scheduler_preproduction_safe()


def test_worker_role_config_rejects_delivery_provider_in_preproduction(monkeypatch) -> None:
    from app.workers import role_config

    monkeypatch.setattr(settings, "OUTBOX_DELIVERY_SINK", "provider")

    with pytest.raises(RuntimeError, match="测试 sink"):
        role_config.assert_test_delivery_sink()


def test_preproduction_scheduler_requires_api_legacy_cron_and_validated_kind(monkeypatch) -> None:
    from app.workers import role_config

    monkeypatch.setattr(settings, "SCHEDULER_ROLE_ENABLED", False)
    monkeypatch.setattr(settings, "SCHEDULER_MIGRATED_KINDS", "")
    with pytest.raises(RuntimeError, match="API 未迁移 cron 必须保持开启"):
        role_config.assert_scheduler_preproduction_safe()

    monkeypatch.setattr(settings, "SCHEDULER_ROLE_ENABLED", True)
    with pytest.raises(RuntimeError, match="必须迁移已验证的 financial_check"):
        role_config.assert_scheduler_preproduction_safe()


def test_worker_probe_fails_when_any_runtime_dependency_is_unavailable(monkeypatch) -> None:
    from app.workers import probe

    monkeypatch.setattr(
        probe,
        "worker_migration_status",
        lambda: {"version": "worker-runtime-001", "ready": True, "stores": {}},
    )
    monkeypatch.setattr(probe, "_check_redis", lambda: "unavailable")

    result = probe.check_worker_dependencies()

    assert result["ready"] is False
    assert result["checks"]["schema"]["ready"] is True
    assert result["checks"]["redis"] == "unavailable"
