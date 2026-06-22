"""定时报告服务 — 管理定时生成风险报告的任务。"""

import uuid
from datetime import datetime, timezone

from app.db.mongo import get_db
from app.core.logging import get_logger

logger = get_logger()

COLLECTION = "scheduled_reports"


def create_scheduled_report(
    company_names: list[str],
    cron: str = "weekly",
    report_type: str = "excel",
) -> dict:
    """创建定时报告任务。"""
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
    db[COLLECTION].insert_one(doc)
    logger.info("scheduled_report_created", task_id=task_id, companies=company_names)
    return {"task_id": task_id, "cron": cron, "report_type": report_type, "company_names": company_names}


def list_scheduled_reports() -> list[dict]:
    """列出所有定时报告任务。"""
    db = get_db()
    docs = list(db[COLLECTION].find({}, {"_id": 0}))
    return docs


def delete_scheduled_report(task_id: str) -> dict:
    """删除定时报告任务。"""
    db = get_db()
    result = db[COLLECTION].delete_one({"task_id": task_id})
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
