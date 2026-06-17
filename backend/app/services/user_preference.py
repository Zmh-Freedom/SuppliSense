"""用户偏好服务 — 记录用户关注行业、偏好指标、最近查询企业。"""

from datetime import datetime, timezone

from pydantic import BaseModel

from app.db.mongo import get_db
from app.core.logging import get_logger

logger = get_logger()

COLLECTION = "user_preferences"
MAX_RECENT = 20


class UserPreference(BaseModel):
    user_id: str
    focused_industries: list[str] = []
    preferred_metrics: list[str] = []
    report_format: str = "excel"
    recent_companies: list[str] = []
    updated_at: datetime | None = None


def get_preference(user_id: str) -> dict:
    """获取用户偏好，不存在则返回默认值。"""
    db = get_db()
    doc = db[COLLECTION].find_one({"user_id": user_id}, {"_id": 0})
    if doc:
        return doc
    return {
        "user_id": user_id,
        "focused_industries": [],
        "preferred_metrics": [],
        "report_format": "excel",
        "recent_companies": [],
    }


def update_preference(user_id: str, data: dict) -> dict:
    """更新用户偏好（部分更新）。"""
    db = get_db()
    data["updated_at"] = datetime.now(timezone.utc)
    db[COLLECTION].update_one(
        {"user_id": user_id},
        {"$set": data},
        upsert=True,
    )
    logger.info("user_preference_updated", user_id=user_id, keys=list(data.keys()))
    return {"user_id": user_id, **data}


def add_recent_company(user_id: str, company_name: str) -> dict:
    """将企业添加到最近查询列表（最多 MAX_RECENT 个）。"""
    db = get_db()
    doc = db[COLLECTION].find_one({"user_id": user_id}) or {}
    recent = doc.get("recent_companies", [])

    # 移除已有的同名企业，添加到最前面
    recent = [c for c in recent if c != company_name]
    recent.insert(0, company_name)
    recent = recent[:MAX_RECENT]

    db[COLLECTION].update_one(
        {"user_id": user_id},
        {"$set": {"recent_companies": recent, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return {"user_id": user_id, "recent_companies": recent}
