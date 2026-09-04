"""审批恢复的 PostgreSQL 兼容适配层。

只保存和消费可序列化恢复元数据；编译后的图对象不进入进程内状态。
后端重启后由调用方按 mode 重建图，PostgreSQL 是唯一事实源。
"""

from typing import Any

from app.core.logging import get_logger

logger = get_logger()


def store(
    session_id: str,
    graph,
    config: dict,
    mode: str,
    user_message: str,
    user_id: str | None = None,
) -> None:
    """保存可恢复元数据；graph 参数仅为旧调用方兼容，不会被缓存。"""
    del graph
    from app.domains.agent_run.chat_interrupt_repo import save_chat_interrupt

    try:
        try:
            save_chat_interrupt(session_id, config, mode, user_message, user_id=user_id)
        except TypeError as exc:
            # Keep older test doubles and third-party compatibility callers
            # working while the real repository uses the user-bound schema.
            if "unexpected keyword argument 'user_id'" not in str(exc):
                raise
            save_chat_interrupt(session_id, config, mode, user_message)
    except Exception as exc:
        # Keep legacy installations without the control plane usable. The
        # durable Agent Run V2 path never depends on this compatibility layer.
        logger.warning("chat_interrupt_durable_save_failed", session_id=session_id, error=str(exc))
def pop(session_id: str) -> dict[str, Any] | None:
    """消费 PostgreSQL 中的恢复元数据。"""
    from app.domains.agent_run.chat_interrupt_repo import take_chat_interrupt

    try:
        durable = take_chat_interrupt(session_id)
    except Exception as exc:
        logger.warning("chat_interrupt_durable_take_failed", session_id=session_id, error=str(exc))
        durable = None
    return durable


def exists(session_id: str) -> bool:
    """从 PostgreSQL 检查待恢复元数据，不读取进程内状态。"""
    from app.domains.agent_run.chat_interrupt_repo import has_chat_interrupt

    try:
        return has_chat_interrupt(session_id)
    except Exception as exc:
        logger.warning("chat_interrupt_exists_failed", session_id=session_id, error=str(exc))
        return False
