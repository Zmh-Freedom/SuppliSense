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
    """Normalize references by supplier name without dropping contact evidence."""
    result: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    for reference in references:
        name = str(reference.get("name", "")).strip()
        if not name or name in _GENERIC_COMPANY_NAMES:
            continue
        existing = by_name.get(name)
        if not existing:
            item = {**reference, "name": name}
            result.append(item)
            by_name[name] = item
            continue
        for field in (
            "website_url", "contact_phone", "contact_email", "website_status",
            "contact_status", "website_url_source", "contact_phone_source",
            "contact_email_source", "discovery_source", "candidate_id",
            "result_id", "candidate_type", "identity_status", "company_id",
            "risk_level", "risk_score",
        ):
            if not existing.get(field) and reference.get(field):
                existing[field] = reference[field]
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

    references: list[dict[str, Any]] = []
    source = tool_name or "Agent 工具结果"

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key in ("supplier_name", "company_name"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    reference = {
                        "name": candidate.strip(),
                        "kind": "supplier",
                        "source": source,
                    }
                    for field in (
                        "source", "website_url", "website_status", "website_url_source",
                        "contact_phone", "contact_phone_source", "contact_email",
                        "contact_email_source", "contact_status", "candidate_id",
                        "result_id", "candidate_type", "identity_status", "company_id",
                        "risk_level", "risk_score",
                    ):
                        if item.get(field):
                            reference["discovery_source" if field == "source" else field] = item[field]
                    references.append(reference)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return _dedupe_references(references)


def _load_history(session_id: str) -> list[dict]:
    """加载会话历史消息。"""
    return _load_conversation_context(session_id)["history"]


def _load_conversation_context(session_id: str) -> dict[str, Any]:
    """加载消息与会话级供应商引用，兼容旧会话文档。"""
    db = get_db()
    doc = db["conversations"].find_one({"session_id": session_id})
    if not doc:
        return {"history": [], "references": [], "state": {}}
    messages = doc.get("messages", [])
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in {"user", "assistant"} and isinstance(m.get("content"), str)
    ]
    references = _dedupe_references([
        reference
        for reference in doc.get("references", [])
        if isinstance(reference, dict) and isinstance(reference.get("name"), str)
    ])
    # Prefer the structured references saved with the most recent assistant turn.
    # This keeps tool output (including contact evidence) stable and avoids
    # losing references when the natural-language answer omits company names.
    recent_references: list[dict[str, Any]] = []
    for message in reversed(messages):
        if message["role"] != "assistant":
            continue
        saved_references = message.get("references")
        if isinstance(saved_references, list):
            recent_references = _dedupe_references([
                reference for reference in saved_references if isinstance(reference, dict)
            ])
        if not recent_references and isinstance(message.get("content"), str):
            recent_references = extract_supplier_references(
                message["content"], "conversation_history"
            )
        if recent_references:
            break
    if recent_references:
        references = _dedupe_references(recent_references)
    state = doc.get("conversation_state")
    return {
        "history": history,
        "references": references,
        "state": state if isinstance(state, dict) else {},
    }


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
        previous_doc = db["conversations"].find_one(
            {"session_id": session_id}, {"conversation_state": 1}
        )
        previous_state = (
            previous_doc.get("conversation_state")
            if isinstance(previous_doc, dict)
            and isinstance(previous_doc.get("conversation_state"), dict)
            else {}
        )
        normalized_references = _dedupe_references(references or [])
        from app.services.conversation_state import build_conversation_state

        conversation_state = build_conversation_state(
            user_msg, normalized_references, previous_state, session_id=session_id
        )
        assistant_message: dict[str, Any] = {
            "role": "assistant", "content": assistant_msg,
        }
        if normalized_references:
            assistant_message["references"] = normalized_references
        update: dict[str, Any] = {
            "$push": {
                "messages": {
                    "$each": [
                        {"role": "user", "content": user_msg},
                        assistant_message,
                    ]
                }
            },
            "$setOnInsert": {"session_id": session_id, "created_at": now},
            "$set": {"conversation_state": conversation_state, "updated_at": now},
        }
        if normalized_references:
            update["$addToSet"] = {"references": {"$each": normalized_references}}
        db["conversations"].update_one(
            {"session_id": session_id},
            update,
            upsert=True,
        )
    except Exception:
        logger.exception("save_turn_failed", session_id=session_id)
