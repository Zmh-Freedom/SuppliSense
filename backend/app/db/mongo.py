import os

from pymongo import MongoClient
from pymongo.database import Database

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
        )
    return _client[os.getenv("MONGO_DB", "tianyancha")]
