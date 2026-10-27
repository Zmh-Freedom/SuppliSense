"""Database cleanup must refuse every database except the dedicated Outbox suite."""

from contextlib import contextmanager

import pytest

from app.core.config import settings
from tests import test_outbox_service as suite


@pytest.mark.parametrize("database", ["sra", "postgres", "run_lifecycle_test"])
def test_outbox_cleanup_refuses_other_databases_before_opening_connections(monkeypatch, database):
    monkeypatch.setattr(settings, "PG_DB", database)
    monkeypatch.setattr(suite, "ensure_pg_schema", lambda: pytest.fail("schema touched before database guard"))
    monkeypatch.setattr(suite, "get_cursor", lambda: pytest.fail("database opened before guard"))
    fixture = suite._isolate_outbox_queue.__wrapped__()
    with pytest.raises(pytest.skip.Exception, match="outbox_service_test"):
        next(fixture)


def test_outbox_cleanup_only_runs_on_explicit_dedicated_database(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "PG_DB", "outbox_service_test")
    monkeypatch.setattr(suite, "ensure_pg_schema", lambda: calls.append("schema"))

    class Cursor:
        def execute(self, statement):
            calls.append(statement)

    @contextmanager
    def cursor():
        yield None, Cursor()

    monkeypatch.setattr(suite, "get_cursor", cursor)
    fixture = suite._isolate_outbox_queue.__wrapped__()
    next(fixture)
    fixture.close()
    assert calls == ["schema", "TRUNCATE outbox_consumptions, outbox_events"]
