import logging

from pymongo import MongoClient
from pymongo.asynchronous.mongo_client import AsyncMongoClient
from pymongo.database import Database
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: MongoClient | None = None
_async_client: AsyncMongoClient | None = None


def get_db() -> Database:
    """同步 MongoDB 连接（兼容旧代码）。"""
    global _client
    if _client is None:
        _client = MongoClient(
            host=settings.MONGO_HOST,
            port=settings.MONGO_PORT,
            username=settings.MONGO_USER,
            password=settings.MONGO_PASSWORD,
            authSource=settings.MONGO_AUTH_SOURCE,
            serverSelectionTimeoutMS=5000,
            maxPoolSize=50,
            minPoolSize=5,
            maxIdleTimeMS=30000,
        )
    return _client[settings.MONGO_DB]


def get_async_db() -> AsyncDatabase:
    """异步 MongoDB 连接 — 用于 FastAPI 异步端点。

    无需 asyncio.to_thread 包装，直接在 async handler 中 await。
    """
    global _async_client
    if _async_client is None:
        _async_client = AsyncMongoClient(
            host=settings.MONGO_HOST,
            port=settings.MONGO_PORT,
            username=settings.MONGO_USER,
            password=settings.MONGO_PASSWORD,
            authSource=settings.MONGO_AUTH_SOURCE,
            serverSelectionTimeoutMS=5000,
            maxPoolSize=50,
            minPoolSize=5,
            maxIdleTimeMS=30000,
        )
    return _async_client[settings.MONGO_DB]


def close_db() -> None:
    """优雅关闭 MongoDB 连接。"""
    global _client, _async_client
    if _client is not None:
        _client.close()
        _client = None
    if _async_client is not None:
        _async_client.close()
        _async_client = None
    logger.info("MongoDB connections closed")


def ensure_indexes() -> None:
    """创建常用查询索引（幂等操作）。"""
    try:
        db = get_db()

        def ensure_index(collection_name: str, keys: list[tuple[str, int]], **options: object) -> None:
            """Avoid startup noise when an older index has compatible keys."""
            collection = db[collection_name]
            index_name = str(options.get("name") or "_".join(f"{field}_{direction}" for field, direction in keys))
            existing = collection.index_information().get(index_name)
            if existing:
                existing_keys = [(str(field), int(direction)) for field, direction in existing.get("key", [])]
                if existing_keys == keys:
                    return
            collection.create_index(keys, **options)

        # baseinfo: 按企业名查询
        ensure_index("baseinfo", [("name", 1)], unique=True, background=True)
        # alert_snapshots: stable monitoring target + time; company_name remains compatibility
        db["alert_snapshots"].create_index(
            [("monitor_target_id", 1), ("checked_at", -1)],
            background=True,
        )
        db["alert_snapshots"].create_index(
            [("monitor_target_id", 1), ("snapshot_version", -1)],
            background=True,
        )
        db["alert_snapshots"].create_index(
            [("company_name", 1), ("checked_at", -1)],
            background=True,
        )
        db["alert_snapshots"].create_index(
            [("company_name", 1), ("snapshot_version", -1)],
            background=True,
        )
        # watchlist: stable monitoring target identities; company_name remains a compatibility index
        db["watchlist"].create_index([("monitor_target_id", 1)], unique=True, sparse=True, background=True)
        db["watchlist"].create_index([("supplier_id", 1)], background=True)
        db["watchlist"].create_index([("candidate_id", 1)], background=True)
        db["watchlist"].create_index([("company_id", 1)], background=True)
        ensure_index("watchlist", [("company_name", 1)], background=True)
        # V2 approved actions: durable external side-effect idempotency
        db["suppliers"].create_index(
            [("agent_action_key", 1)], unique=True, sparse=True, background=True
        )
        db["access_applications"].create_index(
            [("agent_action_key", 1)], unique=True, sparse=True, background=True
        )
        db["external_supplier_candidates"].create_index(
            [("status", 1), ("updated_at", -1)], background=True
        )
        db["supplier_master_snapshots"].create_index(
            [("source", 1), ("source_record_id", 1)], unique=True, background=True
        )
        db["supplier_master_snapshots"].create_index(
            [("name", 1), ("status", 1)], background=True
        )
        db["supplier_capability_snapshots"].create_index(
            [("source", 1), ("source_record_id", 1)], unique=True, background=True
        )
        db["supplier_capability_snapshots"].create_index(
            [("supplier_id", 1), ("category", 1)], background=True
        )
        db["supplier_contact_snapshots"].create_index(
            [("source", 1), ("source_record_id", 1)], unique=True, background=True
        )
        db["supplier_contact_snapshots"].create_index(
            [("supplier_id", 1), ("is_primary_contact", 1)], background=True
        )
        db["supplier_transaction_snapshots"].create_index(
            [("source", 1), ("source_record_id", 1)], unique=True, background=True
        )
        db["supplier_transaction_snapshots"].create_index(
            [
                ("supplier_code", 1),
                ("snapshot_month", -1),
                ("sync_status", 1),
                ("data_mode", 1),
            ],
            background=True,
        )
        db["supplier_transaction_snapshots"].create_index(
            [
                ("category_code", 1),
                ("purchasing_org_code", 1),
                ("base", 1),
                ("snapshot_month", -1),
            ],
            background=True,
        )
        db["feishu_supplier_identity_map"].create_index(
            [("source_system", 1), ("supplier_code", 1)], unique=True, background=True
        )
        db["feishu_supplier_identity_map"].create_index(
            [("source_system", 1), ("source_record_id", 1)], unique=True, background=True
        )
        db["agent_report_exports"].create_index(
            [("agent_action_key", 1)], unique=True, sparse=True, background=True
        )
        db["agent_evidence_payloads"].create_index(
            [("raw_payload_ref", 1)], unique=True, sparse=True, background=True
        )
        # alerts: 按时间倒序查询
        db["alerts"].create_index([("created_at", -1)], background=True)
        # sentiment_results: 按企业+时间查询
        db["sentiment_results"].create_index(
            [("company_name", 1), ("analyzed_at", -1)],
            background=True,
        )
        # financial_cache: 按企业名+报告期
        db["financial_cache"].create_index(
            [("company_name", 1), ("report_date", -1)],
            background=True,
        )
        # supply_deps: 按供应商查询
        db["supply_deps"].create_index([("supplier", 1)], background=True)
        # conversations: 按 session+时间
        db["conversations"].create_index(
            [("session_id", 1), ("created_at", 1)],
            background=True,
        )
        # notifications: 按已读状态+时间
        db["notifications"].create_index(
            [("read", 1), ("created_at", -1)], background=True
        )
        db["notifications"].create_index(
            [("purchaser_open_id", 1), ("created_at", -1)], background=True
        )
        db["notifications"].create_index(
            [("manager_open_id", 1), ("created_at", -1)], background=True
        )
        db["notification_deliveries"].create_index(
            [("notification_id", 1), ("created_at", -1)], background=True
        )
        # 天眼查数据集合（按企业名查询，之前缺失）
        _tianyancha_collections = [
            "riskInfo", "lawSuit", "courtRegister", "abnormal", "punishmentInfo", "executedPerson",
            "dishonesty", "equityPledge", "branch", "news", "alert_rules",
        ]
        for col in _tianyancha_collections:
            try:
                db[col].create_index([("name", 1)], background=True)
            except Exception:
                pass
        # alerts: stable monitoring target + time; company_name remains a compatibility field
        db["alerts"].create_index([("monitor_target_id", 1), ("created_at", -1)], background=True)
        db["alerts"].create_index([("company_name", 1), ("created_at", -1)], background=True)
        # api_call_logs: 按日期+时间
        db["api_call_logs"].create_index([("date", 1), ("created_at", -1)], background=True)
        logger.info("MongoDB indexes ensured")
    except Exception as e:
        logger.warning("Failed to ensure indexes: %s", e)
