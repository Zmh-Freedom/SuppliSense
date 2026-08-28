from __future__ import annotations

from datetime import timezone

import httpx

from app.core.config import settings
from app.services import feishu_bitable
from app.services.feishu_bitable import (
    FeishuBitableClient,
    build_supplier_capability_client,
    build_supplier_contact_client,
    build_supplier_master_client,
    build_supplier_transaction_client,
    normalize_supplier_record,
)


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def test_normalize_supplier_record_maps_formal_supplier_fields() -> None:
    result = normalize_supplier_record({
        "record_id": "rec-1",
        "fields": {
            "供应商代码": "S-1",
            "供应商名称": "深圳市示例科技有限公司",
            "统一社会信用代码": "91440300TEST",
            "主营品类": "摄像头模组，电子元器件",
            "经营地区": ["广东", "华东"],
            "是否正式供应商": "是",
            "联系电话": 13800138000,
        },
    })

    assert result is not None
    assert result["_id"] == "feishu:rec-1"
    assert result["supplier_code"] == "S-1"
    assert result["name"] == "深圳市示例科技有限公司"
    assert result["unified_code"] == "91440300TEST"
    assert result["categories"] == ["摄像头模组", "电子元器件"]
    assert result["regions"] == ["广东", "华东"]
    assert result["status"] == "active"
    assert result["contact_phone"] == "13800138000"


def test_bitable_client_reads_all_pages_and_reuses_token(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_request(method: str, url: str, **kwargs):
        if url.endswith("/tenant_access_token/internal"):
            return FakeResponse({"code": 0, "tenant_access_token": "tenant-token", "expire": 7200})
        page_token = kwargs.get("params", {}).get("page_token")
        calls.append((method, page_token))
        if page_token is None:
            return FakeResponse({
                "code": 0,
                "data": {
                    "items": [{"record_id": "rec-1", "fields": {"公司名称": "企业一"}}],
                    "has_more": True,
                    "page_token": "page-2",
                },
            })
        return FakeResponse({
            "code": 0,
            "data": {"items": [{"record_id": "rec-2", "fields": {"公司名称": "企业二"}}], "has_more": False},
        })

    monkeypatch.setattr(httpx, "request", fake_request)
    client = FeishuBitableClient(
        app_id="app",
        app_secret="secret",
        app_token="base",
        table_id="table",
    )

    records = client.list_records()
    assert [record["record_id"] for record in records] == ["rec-1", "rec-2"]
    assert calls == [("GET", None), ("GET", "page-2")]
    assert client.get_tenant_access_token() == "tenant-token"


def test_build_clients_use_independent_supplier_table_ids(monkeypatch) -> None:
    monkeypatch.setattr(settings, "FEISHU_SUPPLIER_MASTER_TABLE_ID", "tbl-master")
    monkeypatch.setattr(settings, "FEISHU_SUPPLIER_CAPABILITY_TABLE_ID", "tbl-capability")
    monkeypatch.setattr(settings, "FEISHU_SUPPLIER_CONTACT_TABLE_ID", "tbl-contact")
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_TABLE_ID", "tbl-transaction")

    assert build_supplier_master_client().table_id == "tbl-master"
    assert build_supplier_capability_client().table_id == "tbl-capability"
    assert build_supplier_contact_client().table_id == "tbl-contact"
    assert build_supplier_transaction_client().table_id == "tbl-transaction"


def test_settings_no_longer_expose_legacy_single_table_id() -> None:
    assert not hasattr(settings, "FEISHU_BITABLE_TABLE_ID")


class FakeCollection:
    name = "supplier_master_snapshots"

    def __init__(self):
        self.updated: list[dict] = []
        self.stale_update: dict | None = None

    def update_one(self, query, update, upsert=False):
        self.updated.append({"query": query, "update": update, "upsert": upsert})

    def update_many(self, query, update):
        self.stale_update = {"query": query, "update": update}


class FakeDatabase:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, name):
        assert name == "supplier_master_snapshots"
        return self.collection


class FakeClient:
    def list_records(self):
        return [
            {"record_id": "rec-1", "fields": {"供应商代码": "S-1", "公司名称": "企业一", "状态": "正式供应商"}},
            {"record_id": "rec-2", "fields": {}},
        ]


def test_sync_supplier_master_persists_valid_records_and_skips_invalid(monkeypatch) -> None:
    collection = FakeCollection()
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: FakeDatabase(collection))
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)

    result = feishu_bitable.sync_supplier_master(FakeClient())

    assert result["status"] == "ok"
    assert result["synced"] == 1
    assert result["skipped"] == 1
    assert collection.updated[0]["query"] == {"source": "feishu_bitable", "source_record_id": "rec-1"}
    assert collection.updated[0]["upsert"] is True
    assert collection.stale_update is not None
    assert collection.updated[0]["update"]["$set"]["synced_at"].tzinfo == timezone.utc


class MultiFakeCollection:
    def __init__(self, name: str):
        self.name = name
        self.documents: list[dict] = []
        self.update_many_calls: list[tuple[dict, dict]] = []

    def find_one(self, query):
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return dict(document)
        return None

    def update_one(self, query, update, upsert=False):
        document = next(
            (item for item in self.documents if all(item.get(key) == value for key, value in query.items())),
            None,
        )
        if document is None:
            if not upsert:
                return
            document = dict(query)
            document.update(update.get("$setOnInsert", {}))
            self.documents.append(document)
        document.update(update.get("$set", {}))

    def update_many(self, query, update):
        self.update_many_calls.append((query, update))
        for document in self.documents:
            if all(document.get(key) != value for key, value in query.items() if not isinstance(value, dict)):
                document.update(update.get("$set", {}))


class MultiFakeDatabase:
    def __init__(self):
        self.collections = {
            name: MultiFakeCollection(name)
            for name in (
                "supplier_master_snapshots",
                "supplier_capability_snapshots",
                "supplier_contact_snapshots",
                "feishu_supplier_identity_map",
            )
        }

    def __getitem__(self, name):
        return self.collections[name]


class RecordsClient:
    def __init__(self, records=None, error=None):
        self.records = records or []
        self.error = error

    def list_records(self):
        if self.error:
            raise self.error
        return self.records


def test_sync_supplier_tables_links_three_snapshots_by_supplier_code(monkeypatch) -> None:
    database = MultiFakeDatabase()
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)
    monkeypatch.setattr(feishu_bitable, "build_supplier_master_client", lambda: RecordsClient([
        {"record_id": "master-1", "fields": {"供应商代码": "S-1", "供应商名称": "企业一", "供应商状态": "正常"}},
    ]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_capability_client", lambda: RecordsClient([
        {"record_id": "cap-1", "fields": {"供应商代码": "S-1", "品类": "摄像头", "产品名称": "模组", "供货区域": ["华南"]}},
        {"record_id": "cap-2", "fields": {"供应商代码": "S-404", "品类": "摄像头", "产品名称": "模组"}},
    ]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_contact_client", lambda: RecordsClient([
        {"record_id": "contact-1", "fields": {"供应商代码": "S-1", "联系人姓名": "张三", "联系人类型": "商务"}},
    ]))

    result = feishu_bitable.sync_supplier_tables()

    assert result["status"] == "ok"
    assert result["tables"]["supplier_master"]["synced"] == 1
    assert result["tables"]["supplier_capability"]["synced"] == 1
    assert result["tables"]["supplier_capability"]["skipped"] == 1
    assert result["tables"]["supplier_contact"]["synced"] == 1
    supplier_id = database.collections["feishu_supplier_identity_map"].documents[0]["supplier_id"]
    assert database.collections["supplier_master_snapshots"].documents[0]["supplier_id"] == supplier_id
    assert database.collections["supplier_capability_snapshots"].documents[0]["supplier_id"] == supplier_id
    assert database.collections["supplier_contact_snapshots"].documents[0]["supplier_id"] == supplier_id
    assert result["errors"][0]["reason"] == "供应商代码缺失或无法关联主数据"


def test_sync_supplier_tables_reports_partial_failure_without_marking_failed_table_stale(monkeypatch) -> None:
    database = MultiFakeDatabase()
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)
    monkeypatch.setattr(feishu_bitable, "build_supplier_master_client", lambda: RecordsClient([
        {"record_id": "master-1", "fields": {"供应商代码": "S-1", "供应商名称": "企业一"}},
    ]))
    monkeypatch.setattr(
        feishu_bitable,
        "build_supplier_capability_client",
        lambda: RecordsClient(error=feishu_bitable.FeishuBitableError("能力表无权限")),
    )
    monkeypatch.setattr(feishu_bitable, "build_supplier_contact_client", lambda: RecordsClient([]))

    result = feishu_bitable.sync_supplier_tables()

    assert result["status"] == "partial_failed"
    assert result["tables"]["supplier_capability"]["status"] == "failed"
    assert database.collections["supplier_capability_snapshots"].update_many_calls == []
    assert result["errors"][0]["table"] == "supplier_capability"


def test_sync_supplier_tables_rejects_swapped_table_schema(monkeypatch) -> None:
    database = MultiFakeDatabase()
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)
    monkeypatch.setattr(feishu_bitable, "build_supplier_master_client", lambda: RecordsClient([
        {"record_id": "master-1", "fields": {"供应商代码": "S-1", "供应商名称": "企业一"}},
    ]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_capability_client", lambda: RecordsClient([
        {"record_id": "contact-1", "fields": {"供应商代码": "S-1", "联系人姓名": "张三"}},
    ]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_contact_client", lambda: RecordsClient([
        {"record_id": "cap-1", "fields": {"供应商代码": "S-1", "品类": "摄像头", "产品名称": "模组"}},
    ]))

    result = feishu_bitable.sync_supplier_tables()

    assert result["status"] == "partial_failed"
    assert result["tables"]["supplier_capability"]["status"] == "invalid_schema"
    assert result["tables"]["supplier_contact"]["status"] == "invalid_schema"
    assert result["tables"]["supplier_capability"]["synced"] == 0
