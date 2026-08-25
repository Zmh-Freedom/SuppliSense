from __future__ import annotations

from datetime import timezone

import httpx

from app.core.config import settings
from app.services import feishu_bitable
from app.services.feishu_bitable import FeishuBitableClient, normalize_supplier_record


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
            {"record_id": "rec-1", "fields": {"公司名称": "企业一", "状态": "正式供应商"}},
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
