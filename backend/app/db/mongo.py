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
        # baseinfo: 按企业名查询
        db["baseinfo"].create_index([("name", 1)], unique=True, background=True)
        # alert_snapshots: 按企业+时间查询
        db["alert_snapshots"].create_index(
            [("company_name", 1), ("checked_at", -1)],
            background=True,
        )
        # watchlist: 按企业名查询
        db["watchlist"].create_index([("company_name", 1)], unique=True, background=True)
        # V2 approved actions: durable external side-effect idempotency
        db["suppliers"].create_index(
            [("agent_action_key", 1)], unique=True, sparse=True, background=True
        )
        db["access_applications"].create_index(
            [("agent_action_key", 1)], unique=True, sparse=True, background=True
        )
        db["agent_report_exports"].create_index(
            [("agent_action_key", 1)], unique=True, sparse=True, background=True
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
        # 天眼查数据集合（按企业名查询，之前缺失）
        _tianyancha_collections = [
            "riskInfo", "lawSuit", "abnormal", "punishmentInfo", "executedPerson",
            "dishonesty", "equityPledge", "branch", "news", "alert_rules",
        ]
        for col in _tianyancha_collections:
            try:
                db[col].create_index([("name", 1)], background=True)
            except Exception:
                pass
        # alerts: 按企业名+时间
        db["alerts"].create_index([("company_name", 1), ("created_at", -1)], background=True)
        # api_call_logs: 按日期+时间
        db["api_call_logs"].create_index([("date", 1), ("created_at", -1)], background=True)
        logger.info("MongoDB indexes ensured")
    except Exception as e:
        logger.warning("Failed to ensure indexes: %s", e)
