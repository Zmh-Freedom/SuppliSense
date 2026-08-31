from app.services import tianyancha_client


class _Collection:
    def __init__(self, document: dict | None) -> None:
        self.document = document

    def find_one(self, _query: dict) -> dict | None:
        return self.document


class _Database:
    def __init__(self, document: dict | None) -> None:
        self.collection = _Collection(document)

    def __getitem__(self, name: str) -> _Collection:
        assert name == "courtRegister"
        return self.collection


def test_fetch_company_includes_court_register_judicial_endpoint(monkeypatch) -> None:
    called_paths: list[str] = []
    saved_collections: list[str] = []

    monkeypatch.setattr(tianyancha_client, "TOKEN", "test-token")
    monkeypatch.setattr(
        tianyancha_client,
        "_call",
        lambda path, _company_name: called_paths.append(path) or {"result": {}},
    )
    monkeypatch.setattr(
        tianyancha_client,
        "_save",
        lambda collection, *_args: saved_collections.append(collection),
    )
    monkeypatch.setattr(tianyancha_client, "_fetch_lawsuit_paginated", lambda _name: None)

    assert tianyancha_client.fetch_company("示例供应商") is True
    assert "/services/open/jr/courtRegister/2.0" in called_paths
    assert "courtRegister" in saved_collections


def test_ensure_court_register_fetches_only_when_snapshot_missing(monkeypatch) -> None:
    database = _Database(None)
    called_paths: list[str] = []
    saved: list[tuple] = []
    monkeypatch.setattr(tianyancha_client, "TOKEN", "test-token")
    monkeypatch.setattr(tianyancha_client, "get_db", lambda: database)
    monkeypatch.setattr(
        tianyancha_client,
        "_call",
        lambda path, _company_name: called_paths.append(path) or {"result": {}},
    )
    monkeypatch.setattr(
        tianyancha_client,
        "_save",
        lambda *args: saved.append(args),
    )

    assert tianyancha_client.ensure_court_register_evidence("示例供应商") is True
    assert called_paths == ["/services/open/jr/courtRegister/2.0"]
    assert saved[0][0] == "courtRegister"


def test_ensure_court_register_reuses_existing_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(tianyancha_client, "get_db", lambda: _Database({"items": {}}))
    monkeypatch.setattr(
        tianyancha_client,
        "_call",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not call provider")),
    )

    assert tianyancha_client.ensure_court_register_evidence("示例供应商") is True
