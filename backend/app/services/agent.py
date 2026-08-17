import json
from datetime import datetime, timezone
from typing import Any

from app.db.mongo import get_db
from app.core.logging import get_logger

logger = get_logger()


def extract_supplier_references(value: Any, tool_name: str = "") -> list[dict[str, Any]]:
    """Extract stable supplier entities from read-only tool output."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []

    names: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key in ("supplier_name", "company_name"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    names.append(candidate.strip())
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    source = tool_name or "Agent 工具结果"
    return [
        {"name": name, "kind": "supplier", "source": source}
        for name in dict.fromkeys(names)
    ]


def _load_history(session_id: str) -> list[dict]:
    """加载会话历史消息。"""
    return _load_conversation_context(session_id)["history"]


def _load_conversation_context(session_id: str) -> dict[str, list[dict[str, Any]]]:
    """加载消息与会话级供应商引用，兼容旧会话文档。"""
    db = get_db()
    doc = db["conversations"].find_one({"session_id": session_id})
    if not doc:
        return {"history": [], "references": []}
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in doc.get("messages", [])
        if m.get("role") in {"user", "assistant"} and isinstance(m.get("content"), str)
    ]
    references = [
        reference
        for reference in doc.get("references", [])
        if isinstance(reference, dict) and isinstance(reference.get("name"), str)
    ]
    return {"history": history, "references": references}


def _save_turn(
    session_id: str,
    user_msg: str,
    assistant_msg: str,
    references: list[dict[str, Any]] | None = None,
) -> None:
    """保存一轮对话。"""
    if not assistant_msg:
        return
    try:
        db = get_db()
        now = datetime.now(timezone.utc)
        update: dict[str, Any] = {
            "$push": {
                "messages": {
                    "$each": [
                        {"role": "user", "content": user_msg},
                        {"role": "assistant", "content": assistant_msg},
                    ]
                }
            },
            "$setOnInsert": {"session_id": session_id, "created_at": now},
        }
        if references:
            update["$addToSet"] = {"references": {"$each": references}}
        db["conversations"].update_one(
            {"session_id": session_id},
            update,
            upsert=True,
        )
    except Exception:
        logger.exception("save_turn_failed", session_id=session_id)
