"""Live PostgreSQL, MongoDB, and Redis integration checks for CI."""

from uuid import uuid4

import pytest

from app.core.cache import cache_client
from app.db.init_pg import ensure_pg_schema
from app.db.mongo import get_db
from app.db.postgres import get_cursor


pytestmark = pytest.mark.integration


def test_postgres_schema_and_pgvector_are_available() -> None:
    """The CI PostgreSQL service must expose the application schema and vector extension."""
    ensure_pg_schema()

    with get_cursor() as (_, cursor):
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s), "
            "EXISTS (SELECT 1 FROM pg_extension WHERE extname = %s)",
            ("companies", "vector"),
        )
        has_companies, has_vector = cursor.fetchone()

    assert has_companies is True
    assert has_vector is True


def test_mongodb_round_trip_and_index() -> None:
    """The CI MongoDB service must support authenticated writes, reads, and indexes."""
    collection_name = f"_ci_probe_{uuid4().hex}"
    collection = get_db()[collection_name]
    try:
        collection.create_index("probe_id", unique=True)
        collection.insert_one({"probe_id": "ci", "status": "ok"})
        assert collection.find_one({"probe_id": "ci"})["status"] == "ok"
        assert collection.index_information()["probe_id_1"]["unique"] is True
    finally:
        try:
            collection.drop()
        except Exception:
            # Do not hide the primary connection/authentication failure.
            pass


def test_redis_round_trip() -> None:
    """The CI Redis service must support the application's cache database."""
    key = f"_ci_probe:{uuid4().hex}"
    try:
        assert cache_client.set(key, "ok", ex=30) is True
        assert cache_client.get(key) == "ok"
    finally:
        cache_client.delete(key)
