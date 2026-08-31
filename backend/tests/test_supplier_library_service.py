"""Supplier-library read-model tests."""

from app.core.config import settings
from app.domains.sourcing.supplier_repo import get_supplier, list_formal_suppliers, list_suppliers


class FakeCursor:
    def __init__(self, documents: list[dict]):
        self.documents = documents

    def sort(self, _field: str, _direction: int) -> "FakeCursor":
        return self

    def skip(self, _count: int) -> "FakeCursor":
        return self

    def limit(self, _count: int) -> "FakeCursor":
        return self

    def __iter__(self):
        return iter([dict(document) for document in self.documents])


class FakeCollection:
    def __init__(self, name: str, documents: list[dict]):
        self.name = name
        self.documents = documents

    def count_documents(self, _query: dict, limit: int | None = None) -> int:
        count = len(self.documents)
        return min(count, limit) if limit else count

    def find(self, query: dict) -> FakeCursor:
        matched = []
        for document in self.documents:
            if all(
                document.get(key) in value["$in"]
                if isinstance(value, dict) and "$in" in value
                else document.get(key) == value
                for key, value in query.items()
            ):
                matched.append(document)
        return FakeCursor(matched)

    def find_one(self, query: dict) -> dict | None:
        return next(
            (
                dict(document)
                for document in self.documents
                if all(document.get(key) == value for key, value in query.items())
            ),
            None,
        )


class FakeDatabase:
    def __init__(self):
        supplier_id = "supplier:feishu:1"
        self.collections = {
            "supplier_master_snapshots": FakeCollection(
                "supplier_master_snapshots",
                [{
                    "_id": "master-1",
                    "supplier_id": supplier_id,
                    "name": "示例汽车零部件有限公司",
                    "categories": [],
                    "regions": [],
                    "status": "active",
                    "source": "feishu_bitable",
                    "sync_status": "current",
                }],
            ),
            "supplier_capability_snapshots": FakeCollection(
                "supplier_capability_snapshots",
                [{
                    "supplier_id": supplier_id,
                    "source": "feishu_bitable",
                    "sync_status": "current",
                    "category": "汽车零部件",
                    "product_name": "制动卡钳",
                    "product_keywords": ["制动", "卡钳"],
                    "supply_regions": ["华东"],
                }],
            ),
            "supplier_contact_snapshots": FakeCollection("supplier_contact_snapshots", []),
        }

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections[name]


def test_list_suppliers_merges_current_capability_snapshot(monkeypatch) -> None:
    database = FakeDatabase()
    monkeypatch.setattr("app.domains.sourcing.supplier_repo.get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)

    result = list_suppliers(hide_bare=False)

    item = result["items"][0]
    assert item["categories"] == ["汽车零部件"]
    assert item["products"] == ["制动卡钳", "制动", "卡钳"]
    assert item["regions"] == ["华东"]
    assert item["capabilities"][0]["product_name"] == "制动卡钳"


def test_get_supplier_reads_current_feishu_master_by_view_or_stable_id(monkeypatch) -> None:
    database = FakeDatabase()
    monkeypatch.setattr("app.domains.sourcing.supplier_repo.get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)

    by_view_id = get_supplier("master-1")
    by_stable_id = get_supplier("supplier:feishu:1")

    assert by_view_id and by_view_id["name"] == "示例汽车零部件有限公司"
    assert by_stable_id and by_stable_id["_id"] == "master-1"


def test_list_formal_suppliers_reads_active_feishu_directory(monkeypatch) -> None:
    database = FakeDatabase()
    monkeypatch.setattr("app.domains.sourcing.supplier_repo.get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)

    result = list_formal_suppliers()

    assert result["total"] == 1
    assert result["items"] == [{
        "supplier_id": "supplier:feishu:1",
        "supplier_code": None,
        "supplier_name": "示例汽车零部件有限公司",
        "status": "active",
        "categories": ["汽车零部件"],
        "regions": ["华东"],
        "products": ["制动卡钳", "制动", "卡钳"],
        "website_url": None,
        "source": "feishu_bitable",
        "source_updated_at": None,
    }]
