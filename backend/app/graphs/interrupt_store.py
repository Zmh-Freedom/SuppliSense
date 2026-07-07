"""中断图状态存储 — 用于 Human-in-the-Loop 审批恢复。

当 LangGraph interrupt() 触发时，stream 函数将图实例和 config
存储在此，供 resume 端点恢复执行。
"""

from typing import Any

# {session_id: {"graph": compiled_graph, "config": runnable_config, "mode": str, ...}}
_paused: dict[str, dict[str, Any]] = {}


def store(session_id: str, graph, config: dict, mode: str, user_message: str) -> None:
    """保存暂停的图状态。"""
    _paused[session_id] = {
        "graph": graph,
        "config": config,
        "mode": mode,
        "user_message": user_message,
    }


def pop(session_id: str) -> dict[str, Any] | None:
    """取出并删除暂停的图状态。"""
    return _paused.pop(session_id, None)


def exists(session_id: str) -> bool:
    """检查是否存在暂停的图。"""
    return session_id in _paused
