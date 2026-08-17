import json
import re
from datetime import datetime, timezone
from typing import Any

from app.db.mongo import get_db
from app.core.logging import get_logger

logger = get_logger()

_COMPANY_NAME_PATTERN = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,40}(?:有限公司|股份有限公司|股份公司|集团有限公司)"
)
_GENERIC_COMPANY_NAMES = {"公司", "非上市公司", "两家公司", "缺少目标公司", "确认两家目标公司"}


def _dedupe_references(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize references by supplier name, keeping the first source."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for reference in references:
        name = str(reference.get("name", "")).strip()
        if not name or name in _GENERIC_COMPANY_NAMES or name in seen:
            continue
        result.append({**reference, "name": name})
        seen.add(name)
    return result


def extract_supplier_references(value: Any, tool_name: str = "") -> list[dict[str, Any]]:
    """Extract stable supplier entities from read-only tool output."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            names = _COMPANY_NAME_PATTERN.findall(value)
            return _dedupe_references([
                {"name": name, "kind": "supplier", "source": tool_name or "Agent 回答"}
                for name in dict.fromkeys(names)
                if name not in _GENERIC_COMPANY_NAMES
            ])

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
    return _dedupe_references([
        {"name": name, "kind": "supplier", "source": source}
        for name in dict.fromkeys(names)
    ])


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
    references = _dedupe_references([
        reference
        for reference in doc.get("references", [])
        if isinstance(reference, dict) and isinstance(reference.get("name"), str)
    ])
    # Prefer the most recent assistant answer so expressions such as “这两家”
    # refer to the latest selected suppliers instead of every historical result.
    recent_references: list[dict[str, Any]] = []
    for message in reversed(history):
        if message["role"] != "assistant":
            continue
        recent_references = extract_supplier_references(
            message["content"], "conversation_history"
        )
        if recent_references:
            break
    if recent_references:
        references = _dedupe_references(recent_references)
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
