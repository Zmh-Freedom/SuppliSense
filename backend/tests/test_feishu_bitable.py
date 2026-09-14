from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.core.config import settings
from app.services import feishu_bitable
from app.services.feishu_bitable import (
    FeishuBitableClient,
    _upsert_snapshot,
    build_supplier_capability_client,
    build_supplier_contact_client,
    build_supplier_master_client,
    build_supplier_transaction_client,
    normalize_supplier_record,
    normalize_supplier_transaction_record,
)


@pytest.fixture(autouse=True)
def disable_demo_data_freeze_for_sync_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the local demo environment flag from changing unit-test semantics."""
    monkeypatch.setattr(settings, "DEMO_DATA_FREEZE", False)


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
            "公司网址": "https://example.com",
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
    assert result["website_url"] == "https://example.com"


def test_upsert_snapshot_does_not_put_immutable_id_in_set() -> None:
    collection = FakeCollection()

    _upsert_snapshot(
        collection,
        {"_id": "feishu:rec-1", "supplier_code": "S-1"},
        query={"source_record_id": "rec-1"},
    )

    update = collection.updated[0]["update"]
    assert update["$set"] == {"supplier_code": "S-1"}
    assert update["$setOnInsert"] == {"_id": "feishu:rec-1"}


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


def test_normalize_transaction_snapshot_parses_text_numbers_and_marks_synthetic() -> None:
    result = normalize_supplier_transaction_record(
        {
            "record_id": "txn-1",
            "fields": {
                "快照ID": "SYN-TXN-1",
                "统计月份": "2026年08月",
                "快照日期": "2026-08-31",
                "供应商代码": "S-1",
                "采购组织代码": "PO-1",
                "基地": "华南",
                "物料号": "MAT-1",
                "物料名称": "相机模组",
                "计量单位": "件",
                "收货数量": "1,000",
                "正结算数量": "1000",
                "负结算数量": "-10",
                "实结算数量": "990",
                "已结算数量": "800",
                "未结算数量": "190",
                "单价": "12.5",
                "币种": "CNY",
                "金额口径": "含税",
                "合同状态": "履行中",
                "收货金额": "12500",
                "实结算金额": "12375",
                "已结算金额": "10000",
                "未结算金额": "2375",
                "数据来源": "test",
                "数据模式": "synthetic",
                "更新时间": "2026-08-28T10:00:00+08:00",
            },
        },
        supplier_id="supplier:1",
        supplier_code="S-1",
        synced_at=datetime.now(timezone.utc),
    )

    assert result is not None
    assert result["snapshot_month"] == "2026-08"
    assert result["received_qty"] == 1000.0
    assert result["contract_status"] == "active"
    assert result["data_mode"] == "synthetic"
    assert result["eligible_for_formal_assessment"] is False
    assert result["data_quality_status"] == "valid"


def test_normalize_transaction_snapshot_excludes_reconciliation_warning_from_formal_assessment() -> None:
    result = normalize_supplier_transaction_record(
        {
            "record_id": "txn-invalid",
            "fields": {
                "快照ID": "TXN-2", "统计月份": "2026-08", "快照日期": "2026-08-31",
                "供应商代码": "S-1", "采购组织代码": "PO-1", "基地": "华南",
                "物料号": "MAT-1", "计量单位": "件", "收货数量": "10",
                "正结算数量": "10", "负结算数量": "0", "实结算数量": "10",
                "已结算数量": "8", "未结算数量": "2", "单价": "100", "币种": "CNY",
                "金额口径": "含税", "合同状态": "履行中", "收货金额": "1000",
                "实结算金额": "1000", "已结算金额": "800", "未结算金额": "100",
                "数据来源": "erp", "数据模式": "real", "更新时间": "2026-08-28T10:00:00+08:00",
            },
        },
        supplier_id="supplier:1",
        supplier_code="S-1",
        synced_at=datetime.now(timezone.utc),
    )

    assert result is not None
    assert result["data_quality_status"] == "warning"
    assert result["eligible_for_formal_assessment"] is False


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
                "supplier_transaction_snapshots",
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
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_TABLE_ID", "")
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
    assert result["errors"][0]["batch_id"] == result["batch_id"]


def test_sync_supplier_tables_reuses_stable_snapshot_when_feishu_record_id_changes(monkeypatch) -> None:
    database = MultiFakeDatabase()
    database.collections["feishu_supplier_identity_map"].documents.append({
        "_id": "identity-1",
        "source_system": "feishu_bitable",
        "supplier_code": "S-1",
        "source_record_id": "old-master-1",
        "supplier_id": "supplier:feishu:stable-1",
    })
    database.collections["supplier_master_snapshots"].documents.append({
        "_id": "supplier:feishu:stable-1",
        "supplier_id": "supplier:feishu:stable-1",
        "source": "feishu_bitable",
        "source_record_id": "old-master-1",
        "supplier_code": "S-1",
        "name": "企业一",
        "sync_status": "stale",
    })
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_TABLE_ID", "")
    monkeypatch.setattr(feishu_bitable, "build_supplier_master_client", lambda: RecordsClient([
        {"record_id": "new-master-1", "fields": {"供应商代码": "S-1", "供应商名称": "企业一更新"}},
    ]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_capability_client", lambda: RecordsClient([]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_contact_client", lambda: RecordsClient([]))

    result = feishu_bitable.sync_supplier_tables()

    assert result["status"] == "ok"
    snapshot = database.collections["supplier_master_snapshots"].documents[0]
    identity = database.collections["feishu_supplier_identity_map"].documents[0]
    assert snapshot["_id"] == "supplier:feishu:stable-1"
    assert snapshot["source_record_id"] == "new-master-1"
    assert snapshot["name"] == "企业一更新"
    assert identity["source_record_id"] == "new-master-1"


def test_sync_supplier_tables_honors_demo_data_freeze_without_external_clients(monkeypatch) -> None:
    class FrozenCollection:
        def count_documents(self, query):
            assert query == {"source": "feishu_bitable", "sync_status": "current"}
            return 20

    class FrozenDatabase:
        def __getitem__(self, name):
            assert name == "supplier_master_snapshots"
            return FrozenCollection()

    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)
    monkeypatch.setattr(settings, "DEMO_DATA_FREEZE", True)
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: FrozenDatabase())
    monkeypatch.setattr(
        feishu_bitable,
        "build_supplier_master_client",
        lambda: (_ for _ in ()).throw(AssertionError("冻结模式不得创建外部客户端")),
    )

    result = feishu_bitable.sync_supplier_tables()

    assert result == {
        "enabled": True,
        "frozen": True,
        "synced": 0,
        "skipped": 0,
        "current_supplier_count": 20,
        "status": "frozen",
        "message": "比赛演示数据已冻结，未触发飞书同步",
    }


def test_sync_supplier_tables_persists_transaction_snapshot_when_configured(monkeypatch) -> None:
    database = MultiFakeDatabase()
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_TABLE_ID", "tbl-transaction")
    monkeypatch.setattr(feishu_bitable, "build_supplier_master_client", lambda: RecordsClient([
        {"record_id": "master-1", "fields": {"供应商代码": "S-1", "供应商名称": "企业一"}},
    ]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_capability_client", lambda: RecordsClient([]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_contact_client", lambda: RecordsClient([]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_transaction_client", lambda: RecordsClient([
        {
            "record_id": "txn-1",
            "fields": {
                "快照ID": "TXN-1", "统计月份": "2026-08", "快照日期": "2026-08-31",
                "供应商代码": "S-1", "采购组织代码": "PO-1", "基地": "华南",
                "物料号": "MAT-1", "物料名称": "相机模组", "计量单位": "件",
                "收货数量": "10", "正结算数量": "10", "负结算数量": "0",
                "实结算数量": "10", "已结算数量": "8", "未结算数量": "2",
                "单价": "100", "币种": "CNY", "金额口径": "含税", "合同状态": "履行中",
                "收货金额": "1000", "实结算金额": "1000", "已结算金额": "800", "未结算金额": "200",
                "数据来源": "erp", "数据模式": "real", "更新时间": "2026-08-28T10:00:00+08:00",
            },
        },
    ]))

    result = feishu_bitable.sync_supplier_tables()

    assert result["status"] == "ok"
    assert result["tables"]["supplier_transaction"]["synced"] == 1
    saved = database.collections["supplier_transaction_snapshots"].documents[0]
    assert saved["supplier_code"] == "S-1"
    assert saved["data_mode"] == "real"
    assert saved["eligible_for_formal_assessment"] is True


def test_normalize_supplier_transaction_record_maps_supplier_month_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "FEISHU_BITABLE_TRANSACTION_DATA_MODE",
        "real",
    )

    result = normalize_supplier_transaction_record(
        {
            "record_id": "monthly-1",
            "fields": {
                "供应商代码": "08370069",
                "供应商名称": "青岛三祥科技股份有限公司",
                "年月": "202605",
                "收货记录数": "27",
                "实结算金额": "60,430.19",
            },
        },
        supplier_id="supplier:1",
        supplier_code="08370069",
        synced_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )

    assert result is not None
    assert result["supplier_code"] == "08370069"
    assert result["snapshot_month"] == "2026-05"
    assert result["data_granularity"] == "supplier_month"
    assert result["received_record_count"] == 27
    assert result["received_qty"] is None
    assert result["actual_settlement_amount"] == 60430.19
    assert result["data_mode"] == "real"
    assert result["eligible_for_formal_assessment"] is True


def test_normalize_supplier_month_summary_rejects_invalid_record_count(monkeypatch) -> None:
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_DATA_MODE", "real")

    result = normalize_supplier_transaction_record(
        {
            "record_id": "monthly-invalid",
            "fields": {
                "供应商代码": "S-1",
                "供应商名称": "企业一",
                "年月": "2026-08",
                "收货记录数": "1.5",
                "实结算金额": "-100",
            },
        },
        supplier_id="supplier:1",
        supplier_code="S-1",
        synced_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )

    assert result is not None
    assert result["actual_settlement_amount"] == -100
    assert result["eligible_for_formal_assessment"] is False
    assert "收货记录数必须为非负整数" in result["validation_errors"]


def test_sync_supplier_tables_accepts_supplier_month_summary_schema(monkeypatch) -> None:
    database = MultiFakeDatabase()
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_TABLE_ID", "tbl-monthly")
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_DATA_MODE", "real")
    monkeypatch.setattr(feishu_bitable, "build_supplier_master_client", lambda: RecordsClient([
        {"record_id": "master-1", "fields": {"供应商代码": "S-1", "供应商名称": "企业一"}},
    ]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_capability_client", lambda: RecordsClient([]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_contact_client", lambda: RecordsClient([]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_transaction_client", lambda: RecordsClient([
        {
            "record_id": "monthly-1",
            "fields": {
                "供应商代码": "S-1",
                "供应商名称": "企业一",
                "年月": "202608",
                "收货记录数": 12,
                "实结算金额": 1000,
            },
        },
    ]))

    result = feishu_bitable.sync_supplier_tables()

    assert result["status"] == "ok"
    assert result["tables"]["supplier_transaction"]["synced"] == 1
    saved = database.collections["supplier_transaction_snapshots"].documents[0]
    assert saved["data_granularity"] == "supplier_month"
    assert saved["snapshot_month"] == "2026-08"
    assert saved["received_record_count"] == 12


def test_sync_supplier_tables_rejects_duplicate_supplier_month_rows(monkeypatch) -> None:
    database = MultiFakeDatabase()
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_TABLE_ID", "tbl-monthly")
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_DATA_MODE", "real")
    monkeypatch.setattr(feishu_bitable, "build_supplier_master_client", lambda: RecordsClient([
        {"record_id": "master-1", "fields": {"供应商代码": "S-1", "供应商名称": "企业一"}},
    ]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_capability_client", lambda: RecordsClient([]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_contact_client", lambda: RecordsClient([]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_transaction_client", lambda: RecordsClient([
        {"record_id": "monthly-1", "fields": {"供应商代码": "S-1", "供应商名称": "企业一", "年月": "202608", "收货记录数": 12, "实结算金额": 1000}},
        {"record_id": "monthly-2", "fields": {"供应商代码": "S-1", "供应商名称": "企业一", "年月": "2026-08", "收货记录数": 1, "实结算金额": 20}},
    ]))

    result = feishu_bitable.sync_supplier_tables()

    assert result["status"] == "partial_failed"
    assert result["tables"]["supplier_transaction"]["status"] == "invalid_data"
    assert database.collections["supplier_transaction_snapshots"].documents == []
    assert "S-1/2026-08" in result["errors"][0]["reason"]


def test_sync_supplier_tables_reports_partial_failure_without_marking_failed_table_stale(monkeypatch) -> None:
    database = MultiFakeDatabase()
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_TABLE_ID", "")
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
    assert result["errors"][0]["batch_id"] == result["batch_id"]


def test_sync_supplier_tables_rejects_swapped_table_schema(monkeypatch) -> None:
    database = MultiFakeDatabase()
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_TABLE_ID", "")
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


def test_sync_supplier_tables_does_not_write_master_when_master_schema_is_invalid(monkeypatch) -> None:
    database = MultiFakeDatabase()
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_TABLE_ID", "")
    monkeypatch.setattr(feishu_bitable, "build_supplier_master_client", lambda: RecordsClient([
        {"record_id": "master-1", "fields": {"供应商代码": "S-1"}},
    ]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_capability_client", lambda: RecordsClient([]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_contact_client", lambda: RecordsClient([]))

    result = feishu_bitable.sync_supplier_tables()

    assert result["status"] == "partial_failed"
    assert result["tables"]["supplier_master"]["status"] == "invalid_schema"
    assert database.collections["supplier_master_snapshots"].documents == []
    assert database.collections["supplier_master_snapshots"].update_many_calls == []
    assert all(error["batch_id"] == result["batch_id"] for error in result["errors"])


def test_sync_supplier_tables_blocks_duplicate_master_codes_as_invalid_batch(monkeypatch) -> None:
    database = MultiFakeDatabase()
    monkeypatch.setattr(feishu_bitable, "get_db", lambda: database)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_ENABLED", True)
    monkeypatch.setattr(settings, "FEISHU_BITABLE_TRANSACTION_TABLE_ID", "")
    monkeypatch.setattr(feishu_bitable, "build_supplier_master_client", lambda: RecordsClient([
        {"record_id": "master-1", "fields": {"供应商代码": "S-1", "供应商名称": "企业一"}},
        {"record_id": "master-2", "fields": {"供应商代码": "S-1", "供应商名称": "企业一（重复）"}},
    ]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_capability_client", lambda: RecordsClient([
        {"record_id": "cap-1", "fields": {"供应商代码": "S-1", "品类": "摄像头", "产品名称": "模组"}},
    ]))
    monkeypatch.setattr(feishu_bitable, "build_supplier_contact_client", lambda: RecordsClient([]))

    result = feishu_bitable.sync_supplier_tables()

    assert result["status"] == "partial_failed"
    assert result["tables"]["supplier_master"]["status"] == "invalid_data"
    assert result["tables"]["supplier_master"]["synced"] == 0
    assert database.collections["supplier_master_snapshots"].documents == []
    assert database.collections["supplier_capability_snapshots"].documents == []
    assert result["errors"][0]["batch_id"] == result["batch_id"]
