"""Versioned PostgreSQL and MongoDB schema prerequisites for worker roles.

Workers only read migration state. The one-shot migration command applies the
existing idempotent schema/index initializers, stores independent checksums in
both databases, and never migrates legacy user rows as a side effect.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import inspect
import json
from typing import Any, Literal

from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.db import init_pg, mongo
from app.db.assessment_schema import ASSESSMENT_DDL
from app.db.postgres import get_cursor
from app.graphs.sourcing_risk_v2 import checkpointer

MigrationStore = Literal["postgres", "mongo"]


class WorkerSchemaMigrationError(RuntimeError):
    """The schema required by independent workers is absent or inconsistent."""


@dataclass(frozen=True)
class WorkerSchemaMigration:
    version: str
    name: str
    postgres_checksum: str
    mongo_checksum: str


def _checksum(version: str, store: MigrationStore) -> str:
    if store == "postgres":
        manifest: Any = {
            "ddl": init_pg.DDL_STATEMENTS,
            "indexes": init_pg.INDEX_STATEMENTS,
            "assessment_ddl": ASSESSMENT_DDL,
            "initializer": inspect.getsource(init_pg.ensure_pg_schema),
            "legacy_role_data_migration": False,
            "checkpointer_schema": settings.AGENT_RUN_CHECKPOINT_SCHEMA,
            "checkpointer_connection": inspect.getsource(checkpointer._connection_string),
            "checkpointer_initializer": inspect.getsource(checkpointer.migrate_checkpoint_schema),
            "checkpointer_schema_initializer": inspect.getsource(checkpointer._ensure_checkpoint_schema),
        }
    else:
        manifest = {"initializer": inspect.getsource(mongo.ensure_indexes)}
    source = json.dumps(
        {"version": version, "store": store, "manifest": manifest},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return sha256(source.encode("utf-8")).hexdigest()


_INITIAL_MIGRATION_VERSION = "worker-runtime-001"
WORKER_SCHEMA_MIGRATIONS = (
    WorkerSchemaMigration(
        version=_INITIAL_MIGRATION_VERSION,
        name="worker-runtime-schema-and-indexes",
        postgres_checksum=_checksum(_INITIAL_MIGRATION_VERSION, "postgres"),
        mongo_checksum=_checksum(_INITIAL_MIGRATION_VERSION, "mongo"),
    ),
)
CURRENT_WORKER_SCHEMA_VERSION = WORKER_SCHEMA_MIGRATIONS[-1].version


def _expected_checksum(store: MigrationStore, migration: WorkerSchemaMigration) -> str:
    return migration.postgres_checksum if store == "postgres" else migration.mongo_checksum


def _ensure_postgres_history_table() -> None:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_schema_migrations (
                version TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


def _read_migration(store: MigrationStore, version: str) -> dict[str, Any] | None:
    if store == "postgres":
        with get_cursor() as (_, cursor):
            cursor.execute("SELECT to_regclass('public.worker_schema_migrations')")
            if cursor.fetchone()[0] is None:
                return None
            cursor.execute(
                "SELECT version, checksum FROM worker_schema_migrations WHERE version = %s",
                (version,),
            )
            row = cursor.fetchone()
        return {"version": row[0], "checksum": row[1]} if row else None

    record = mongo.get_db()["worker_schema_migrations"].find_one(
        {"_id": version}, {"_id": 1, "checksum": 1}
    )
    if record is None:
        return None
    return {"version": str(record.get("_id") or ""), "checksum": record.get("checksum")}


def _list_migration_versions(store: MigrationStore) -> list[str]:
    if store == "postgres":
        with get_cursor() as (_, cursor):
            cursor.execute("SELECT to_regclass('public.worker_schema_migrations')")
            if cursor.fetchone()[0] is None:
                return []
            cursor.execute("SELECT version FROM worker_schema_migrations ORDER BY applied_at, version")
            return [str(row[0]) for row in cursor.fetchall()]

    records = mongo.get_db()["worker_schema_migrations"].find({}, {"_id": 1})
    return [str(record.get("_id") or "") for record in records]


def _unknown_migration_versions(store: MigrationStore) -> list[str]:
    supported = {migration.version for migration in WORKER_SCHEMA_MIGRATIONS}
    return sorted(set(_list_migration_versions(store)) - supported)


def _record_migration(store: MigrationStore, version: str, checksum: str) -> None:
    if store == "postgres":
        with get_cursor() as (_, cursor):
            cursor.execute(
                """
                INSERT INTO worker_schema_migrations (version, checksum)
                VALUES (%s, %s)
                ON CONFLICT (version) DO NOTHING
                """,
                (version, checksum),
            )
        return

    try:
        mongo.get_db()["worker_schema_migrations"].insert_one(
            {
                "_id": version,
                "checksum": checksum,
                "applied_at": datetime.now(timezone.utc),
            }
        )
    except DuplicateKeyError:
        # A concurrent migration may have won the insert; the caller verifies
        # the committed checksum before reporting readiness.
        return


def _apply_postgres_schema() -> None:
    init_pg.ensure_pg_schema(migrate_legacy_roles=False)
    _apply_checkpointer_schema()


def _apply_checkpointer_schema() -> None:
    asyncio.run(checkpointer.migrate_checkpoint_schema())


def _apply_mongo_indexes() -> None:
    mongo.ensure_indexes(strict=True)


def _assert_isolated_migration_targets() -> None:
    for store, database in (("PostgreSQL", settings.PG_DB), ("MongoDB", settings.MONGO_DB)):
        normalized = database.strip().lower()
        if normalized in {"sra", "tianyancha", "postgres"} or not normalized.endswith(("_test", "_preprod")):
            raise WorkerSchemaMigrationError(
                f"拒绝在非隔离数据库执行 3D worker 迁移：{store} target must end with _test or _preprod"
            )


def _validate_record(
    store: MigrationStore,
    migration: WorkerSchemaMigration,
    record: dict[str, Any] | None,
) -> str:
    if record is None:
        return "missing"
    if record.get("version") != migration.version:
        return "version_mismatch"
    if record.get("checksum") != _expected_checksum(store, migration):
        return "checksum_mismatch"
    return "applied"


def _apply_store_migration(store: MigrationStore, migration: WorkerSchemaMigration) -> None:
    existing = _read_migration(store, migration.version)
    state = _validate_record(store, migration, existing)
    if state == "checksum_mismatch" or state == "version_mismatch":
        raise WorkerSchemaMigrationError(f"{store} migration {migration.version} checksum/version mismatch")
    if state == "applied":
        return

    if store == "postgres":
        _apply_postgres_schema()
    else:
        _apply_mongo_indexes()
    _record_migration(store, migration.version, _expected_checksum(store, migration))

    saved = _read_migration(store, migration.version)
    if _validate_record(store, migration, saved) != "applied":
        raise WorkerSchemaMigrationError(f"{store} migration {migration.version} was not committed")


def worker_migration_status() -> dict[str, Any]:
    """Return a non-mutating readiness snapshot for both migration stores."""
    stores: dict[str, dict[str, str]] = {}
    for store in ("postgres", "mongo"):
        state = "applied"
        try:
            for migration in WORKER_SCHEMA_MIGRATIONS:
                current = _validate_record(
                    store, migration, _read_migration(store, migration.version)
                )
                if current != "applied":
                    state = current
                    break
            if state == "applied" and _unknown_migration_versions(store):
                state = "version_mismatch"
        except Exception as exc:
            state = "unavailable"
            stores[store] = {"status": state, "error_type": type(exc).__name__}
            continue
        stores[store] = {"status": state}
    ready = all(item["status"] == "applied" for item in stores.values())
    return {
        "version": CURRENT_WORKER_SCHEMA_VERSION,
        "ready": ready,
        "stores": stores,
    }


def assert_worker_schema_ready() -> None:
    status = worker_migration_status()
    if status["ready"]:
        return
    details = ", ".join(
        f"{store}={result['status']}"
        for store, result in status["stores"].items()
        if result["status"] != "applied"
    )
    raise WorkerSchemaMigrationError(f"worker schema migration gate failed: {details}")


def apply_worker_migrations() -> dict[str, Any]:
    """Apply missing idempotent migrations; never mark a partial run ready."""
    _assert_isolated_migration_targets()
    for store in ("postgres", "mongo"):
        unknown = _unknown_migration_versions(store)
        if unknown:
            raise WorkerSchemaMigrationError(
                f"{store} contains unsupported worker schema versions: {','.join(unknown)}"
            )
    _ensure_postgres_history_table()
    for migration in WORKER_SCHEMA_MIGRATIONS:
        _apply_store_migration("postgres", migration)
        _apply_store_migration("mongo", migration)
    status = worker_migration_status()
    if not status["ready"]:
        raise WorkerSchemaMigrationError("worker schema migration did not complete in both databases")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Versioned schema migration for worker roles")
    parser.add_argument("command", choices=("migrate", "status", "check"))
    args = parser.parse_args(argv)
    try:
        status = apply_worker_migrations() if args.command == "migrate" else worker_migration_status()
    except Exception as exc:
        print(json.dumps({"ready": False, "error_type": type(exc).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    return 0 if status["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
