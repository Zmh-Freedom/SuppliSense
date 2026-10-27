"""定时报告服务 — 管理定时生成风险报告的任务。"""

import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.logging import get_logger
from app.db.mongo import get_db

logger = get_logger()

COLLECTION = "scheduled_reports"


def _ensure_action_key_index(collection: Any) -> None:
    """Require the unique sparse index before a durable idempotent upsert."""
    expected_keys = [("agent_action_key", 1)]

    def is_unique_sparse(spec: dict[str, Any]) -> bool:
        keys = [(str(field), int(direction)) for field, direction in spec.get("key", [])]
        return (
            keys == expected_keys
            and spec.get("unique") is True
            and spec.get("sparse") is True
        )

    indexes = collection.index_information()
    if any(is_unique_sparse(spec) for spec in indexes.values()):
        return
    collection.create_index(
        expected_keys,
        unique=True,
        sparse=True,
        background=True,
    )
    if not any(is_unique_sparse(spec) for spec in collection.index_information().values()):
        raise RuntimeError("定时报告动作幂等唯一索引未就绪")


def create_scheduled_report(
    company_names: list[str],
    cron: str = "weekly",
    report_type: str = "excel",
    *,
    owner_user_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """创建定时报告任务；Durable Action 调用按动作键原子去重。"""
    db = get_db()
    task_id = str(uuid.uuid4())[:8]
    doc = {
        "task_id": task_id,
        "company_names": company_names,
        "cron": cron,
        "report_type": report_type,
        "created_at": datetime.now(timezone.utc),
        "last_run": None,
        "enabled": True,
    }
    if owner_user_id:
        doc["owner_user_id"] = owner_user_id
    if idempotency_key:
        _ensure_action_key_index(db[COLLECTION])
        doc["agent_action_key"] = idempotency_key
        query = {"agent_action_key": idempotency_key}
        if owner_user_id:
            query["owner_user_id"] = owner_user_id
        collection = db[COLLECTION]
        try:
            persisted = collection.find_one_and_update(
                query,
                {"$setOnInsert": doc},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            persisted = collection.find_one(query)
            if persisted is None:
                raise
        if persisted is None:
            raise RuntimeError("定时报告任务持久化失败")
        doc = persisted
    else:
        db[COLLECTION].insert_one(doc)
    logger.info("scheduled_report_created", task_id=doc["task_id"], companies=doc["company_names"])
    return {
        "task_id": doc["task_id"],
        "cron": doc["cron"],
        "report_type": doc["report_type"],
        "company_names": doc["company_names"],
    }


def list_scheduled_reports(*, owner_user_id: str | None = None) -> list[dict]:
    """列出所有定时报告任务。"""
    db = get_db()
    query = {"owner_user_id": owner_user_id} if owner_user_id is not None else {}
    docs = list(db[COLLECTION].find(query, {"_id": 0}))
    return docs


def delete_scheduled_report(task_id: str, *, owner_user_id: str | None = None) -> dict:
    """删除定时报告任务。"""
    db = get_db()
    query = {"task_id": task_id}
    if owner_user_id is not None:
        query["owner_user_id"] = owner_user_id
    result = db[COLLECTION].delete_one(query)
    if result.deleted_count == 0:
        return {"error": f"未找到任务: {task_id}"}
    logger.info("scheduled_report_deleted", task_id=task_id)
    return {"deleted": task_id}


def run_scheduled_report(task_id: str) -> dict:
    """执行一次定时报告任务（由 scheduler 调用）。"""
    db = get_db()
    doc = db[COLLECTION].find_one({"task_id": task_id})
    if not doc:
        return {"error": f"未找到任务: {task_id}"}

    from app.domains.risk.report_service import generate_excel, generate_html_report

    results = []
    for company in doc["company_names"]:
        try:
            if doc["report_type"] == "html":
                content = generate_html_report(company)
                results.append({"company": company, "status": "ok", "format": "html", "length": len(content)})
            else:
                content = generate_excel(company)
                results.append({"company": company, "status": "ok", "format": "excel", "size_bytes": len(content)})
        except Exception as e:
            results.append({"company": company, "status": "error", "error": str(e)})

    db[COLLECTION].update_one(
        {"task_id": task_id},
        {"$set": {"last_run": datetime.now(timezone.utc)}},
    )
    logger.info("scheduled_report_executed", task_id=task_id, results=len(results))
    return {"task_id": task_id, "results": results}
