import logging
import os

from pymongo import MongoClient
from pymongo.database import Database

logger = logging.getLogger(__name__)

_client: MongoClient | None = None


def get_db() -> Database:
    global _client
    if _client is None:
        _client = MongoClient(
            host=os.getenv("MONGO_HOST", "localhost"),
            port=int(os.getenv("MONGO_PORT", "27017")),
            username=os.getenv("MONGO_USER", "root"),
            password=os.getenv("MONGO_PASSWORD", "123456"),
            authSource=os.getenv("MONGO_AUTH_SOURCE", "admin"),
            serverSelectionTimeoutMS=5000,
        )
    return _client[os.getenv("MONGO_DB", "tianyancha")]


def close_db() -> None:
    """优雅关闭 MongoDB 连接。"""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        logger.info("MongoDB connection closed")


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
        logger.info("MongoDB indexes ensured")
    except Exception as e:
        logger.warning("Failed to ensure indexes: %s", e)
