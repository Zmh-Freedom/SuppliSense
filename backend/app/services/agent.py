"""会话历史管理 — LangGraph 架构共享的对话持久化。"""

from datetime import datetime, timezone

from app.db.mongo import get_db
from app.core.logging import get_logger

logger = get_logger()


def _load_history(session_id: str) -> list[dict]:
    """加载会话历史消息。"""
    db = get_db()
    doc = db["conversations"].find_one({"session_id": session_id})
    if not doc:
        return []
    return [{"role": m["role"], "content": m["content"]} for m in doc["messages"]]


def _save_turn(session_id: str, user_msg: str, assistant_msg: str) -> None:
    """保存一轮对话。"""
    if not assistant_msg:
        return
    try:
        db = get_db()
        now = datetime.now(timezone.utc)
        db["conversations"].update_one(
            {"session_id": session_id},
            {
                "$push": {
                    "messages": {
                        "$each": [
                            {"role": "user", "content": user_msg},
                            {"role": "assistant", "content": assistant_msg},
                        ]
                    }
                },
                "$setOnInsert": {"session_id": session_id, "created_at": now},
            },
            upsert=True,
        )
    except Exception:
        logger.exception("save_turn_failed", session_id=session_id)
