from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from app.domains.risk import scheduled_report


class MemoryCollection:
    def __init__(self) -> None:
        self.documents: list[dict] = []
        self.indexes: dict[str, dict] = {}
        self.index_error: Exception | None = None

    def index_information(self):
        return deepcopy(self.indexes)

    def create_index(self, keys, **options):
        if self.index_error is not None:
            raise self.index_error
        name = "_".join(f"{field}_{direction}" for field, direction in keys)
        self.indexes[name] = {"key": list(keys), **options}
        return name

    def insert_one(self, document):
        self.documents.append(deepcopy(document))

    def find_one_and_update(self, query, update, *, upsert, return_document):
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return deepcopy(document)
        if not upsert:
            return None
        document = deepcopy(update["$setOnInsert"])
        self.documents.append(document)
        return deepcopy(document)

    def find(self, query, projection=None):
        result = [
            deepcopy(document)
            for document in self.documents
            if all(document.get(key) == value for key, value in query.items())
        ]
        if projection and projection.get("_id") == 0:
            for document in result:
                document.pop("_id", None)
        return result

    def delete_one(self, query):
        for index, document in enumerate(self.documents):
            if all(document.get(key) == value for key, value in query.items()):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


def _bind_collection(monkeypatch, collection: MemoryCollection) -> None:
    monkeypatch.setattr(scheduled_report, "get_db", lambda: {"scheduled_reports": collection})


def test_scheduled_report_list_and_delete_are_scoped_to_owner(monkeypatch):
    collection = MemoryCollection()
    _bind_collection(monkeypatch, collection)
    own = scheduled_report.create_scheduled_report(
        ["甲公司"], owner_user_id="buyer-a", idempotency_key="proposal-a"
    )
    foreign = scheduled_report.create_scheduled_report(
        ["乙公司"], owner_user_id="buyer-b", idempotency_key="proposal-b"
    )
    legacy = scheduled_report.create_scheduled_report(["丙公司"])

    reports = scheduled_report.list_scheduled_reports(owner_user_id="buyer-a")
    denied = scheduled_report.delete_scheduled_report(
        foreign["task_id"], owner_user_id="buyer-a"
    )

    assert [item["task_id"] for item in reports] == [own["task_id"]]
    assert denied == {"error": f"未找到任务: {foreign['task_id']}"}
    assert any(item["task_id"] == foreign["task_id"] for item in collection.documents)
    assert any(item["task_id"] == legacy["task_id"] for item in collection.documents)


def test_scheduled_report_create_is_idempotent_after_successful_insert(monkeypatch):
    collection = MemoryCollection()
    _bind_collection(monkeypatch, collection)

    first = scheduled_report.create_scheduled_report(
        ["甲公司"], owner_user_id="buyer-a", idempotency_key="proposal-a"
    )
    replay = scheduled_report.create_scheduled_report(
        ["甲公司"], owner_user_id="buyer-a", idempotency_key="proposal-a"
    )

    assert first["task_id"] == replay["task_id"]
    assert len(collection.documents) == 1
    assert collection.documents[0]["agent_action_key"] == "proposal-a"


def test_scheduled_report_durable_create_ensures_unique_action_index(monkeypatch):
    collection = MemoryCollection()
    _bind_collection(monkeypatch, collection)

    scheduled_report.create_scheduled_report(
        ["甲公司"], owner_user_id="buyer-a", idempotency_key="proposal-a"
    )

    index = collection.indexes["agent_action_key_1"]
    assert index["unique"] is True
    assert index["sparse"] is True


def test_scheduled_report_durable_create_fails_closed_if_unique_index_unavailable(monkeypatch):
    collection = MemoryCollection()
    collection.index_error = RuntimeError("index creation failed")
    _bind_collection(monkeypatch, collection)

    import pytest

    with pytest.raises(RuntimeError, match="index creation failed"):
        scheduled_report.create_scheduled_report(
            ["甲公司"], owner_user_id="buyer-a", idempotency_key="proposal-a"
        )

    assert collection.documents == []
