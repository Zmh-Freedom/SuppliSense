"""审批恢复兼容层。

PostgreSQL 保存可序列化的恢复元数据；进程内仅缓存编译后的图对象，
用于同进程的快速恢复。重启后的恢复由调用方按 mode 重建图。
"""

from typing import Any

from app.core.logging import get_logger

# {session_id: {"graph": compiled_graph, "config": runnable_config, "mode": str, ...}}
_paused: dict[str, dict[str, Any]] = {}
logger = get_logger()


def store(session_id: str, graph, config: dict, mode: str, user_message: str) -> None:
    """保存可恢复元数据，并缓存图对象作为兼容优化。"""
    from app.domains.agent_run.chat_interrupt_repo import save_chat_interrupt

    try:
        save_chat_interrupt(session_id, config, mode, user_message)
    except Exception as exc:
        # Keep legacy installations without the control plane usable. The
        # durable Agent Run V2 path never depends on this compatibility layer.
        logger.warning("chat_interrupt_durable_save_failed", session_id=session_id, error=str(exc))
    _paused[session_id] = {
        "graph": graph,
        "config": config,
        "mode": mode,
        "user_message": user_message,
    }


def pop(session_id: str) -> dict[str, Any] | None:
    """优先取进程缓存，同时消费 PostgreSQL 中的恢复元数据。"""
    cached = _paused.pop(session_id, None)
    from app.domains.agent_run.chat_interrupt_repo import take_chat_interrupt

    try:
        durable = take_chat_interrupt(session_id)
    except Exception as exc:
        logger.warning("chat_interrupt_durable_take_failed", session_id=session_id, error=str(exc))
        durable = None
    if cached is not None:
        if durable:
            cached.update({key: value for key, value in durable.items() if key != "session_id"})
        return cached
    return durable


def exists(session_id: str) -> bool:
    """检查是否存在暂停的图。"""
    return session_id in _paused
