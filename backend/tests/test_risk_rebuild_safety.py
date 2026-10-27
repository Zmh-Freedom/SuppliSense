"""Regression tests for non-destructive score rebuilds."""

from unittest.mock import MagicMock

import pytest

from app.domains.risk import service


def test_rebuild_aborts_when_watchlist_read_fails(monkeypatch) -> None:
    db = {"watchlist": MagicMock(), "alert_snapshots": MagicMock(), "alerts": MagicMock()}
    db["watchlist"].find.side_effect = RuntimeError("mongo unavailable")
    monkeypatch.setattr("app.db.mongo.get_db", lambda: db)

    with pytest.raises(ValueError, match="监控名单"):
        service.rebuild_all_supplier_scores()

    db["alert_snapshots"].delete_many.assert_not_called()
    db["alerts"].delete_many.assert_not_called()


def test_rebuild_aborts_when_supplier_source_is_empty(monkeypatch) -> None:
    db = {"watchlist": MagicMock(), "alert_snapshots": MagicMock(), "alerts": MagicMock()}
    db["watchlist"].find.return_value = []
    monkeypatch.setattr("app.db.mongo.get_db", lambda: db)
    monkeypatch.setattr(
        "app.domains.sourcing.supplier_repo._supplier_read_collection", lambda _db: MagicMock()
    )
    monkeypatch.setattr(
        "app.domains.sourcing.supplier_repo._current_supplier_filter", lambda _collection: {}
    )

    with pytest.raises(ValueError, match="重建名单为空"):
        service.rebuild_all_supplier_scores()

    db["alert_snapshots"].delete_many.assert_not_called()
    db["alerts"].delete_many.assert_not_called()


def test_rebuild_keeps_existing_data_when_one_supplier_fails(monkeypatch) -> None:
    db = {"watchlist": MagicMock(), "alert_snapshots": MagicMock(), "alerts": MagicMock()}
    db["watchlist"].find.return_value = [{"company_name": "甲公司"}]
    monkeypatch.setattr("app.db.mongo.get_db", lambda: db)
    monkeypatch.setattr(
        "app.domains.sourcing.supplier_repo._supplier_read_collection", lambda _db: MagicMock()
    )
    monkeypatch.setattr(
        "app.domains.sourcing.supplier_repo._current_supplier_filter", lambda _collection: {}
    )
    monkeypatch.setattr(
        service, "assess_risk", lambda _request: (_ for _ in ()).throw(RuntimeError("assessment failed"))
    )

    result = service.rebuild_all_supplier_scores()

    assert result["failed"] == 1
    assert result["removed_snapshots"] == 0
    assert result["removed_alerts"] == 0
    db["alert_snapshots"].delete_many.assert_not_called()
    db["alerts"].delete_many.assert_not_called()
