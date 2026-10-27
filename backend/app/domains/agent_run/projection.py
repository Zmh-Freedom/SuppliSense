"""Rebuild Mongo conversation display from committed PostgreSQL turns."""

from datetime import datetime, timezone
from typing import Any

from app.db.mongo import get_db
from app.db.postgres import get_cursor
from app.domains.agent_run.repo import _row_to_dict


def project_chat_completion(event: dict[str, Any]) -> None:
    session_id = str(event["payload"]["session_id"])
    with get_cursor() as (_, cur):
        cur.execute("SELECT state,version FROM agent_sessions WHERE id=%s FOR SHARE", (session_id,))
        session = _row_to_dict(cur, cur.fetchone())
        if session is None:
            return
        context = session["state"].get("execution_context") or {}
        cur.execute(
            """SELECT run_id,user_message,assistant_message,response FROM agent_turns
               WHERE session_id=%s AND turn_number >= %s
               AND status IN ('completed','partial','failed','cancelled') ORDER BY turn_number""",
            (session_id, int(session["state"].get("projection_start_turn", 1))),
        )
        turns = [_row_to_dict(cur, row) for row in cur.fetchall()]
    messages = []
    for turn in turns:
        marker = str(turn["run_id"])
        messages.append({"role": "user", "content": turn["user_message"], "_control_plane_run_id": marker})
        if turn["assistant_message"]:
            messages.append({
                "role": "assistant", "content": turn["assistant_message"],
                "references": (turn.get("response") or {}).get("references", []),
                "_control_plane_run_id": marker,
            })
    version = int(session["version"])
    # Pipeline update makes late/repeated delivery harmless even if two
    # consumers read different PG revisions before writing Mongo concurrently.
    newer = {"$lt": [{"$ifNull": ["$_control_plane_version", -1]}, version]}
    values = {
        "session_id": session_id, "messages": messages,
        "references": context.get("references", []),
        "conversation_state": context.get("conversation_state", {}),
        "_control_plane_version": version,
        "updated_at": datetime.now(timezone.utc),
    }
    update = {key: {"$cond": [newer, {"$literal": value}, f"${key}"]} for key, value in values.items()}
    legacy_messages = {"$filter": {
        "input": {"$ifNull": ["$messages", []]}, "as": "message",
        "cond": {"$eq": [{"$type": "$$message._control_plane_run_id"}, "missing"]},
    }}
    update["messages"] = {"$cond": [newer, {"$concatArrays": [legacy_messages, {"$literal": messages}]}, "$messages"]}
    update["created_at"] = {"$ifNull": ["$created_at", {"$literal": datetime.now(timezone.utc)}]}
    get_db()["conversations"].update_one({"session_id": session_id}, [{"$set": update}], upsert=True)
