"""Human-in-the-Loop 审批模块。

高风险操作（加入/移出监控清单、删除定时报告、申请准入）
在执行前需要用户确认。使用 LangGraph interrupt() 暂停图执行，
等待用户通过 resume 端点确认后继续。

审批流程：
  1. Agent 调用高风险工具 → tool 内部调用 request_approval()
  2. request_approval() 调用 interrupt() 暂停图
  3. SSE 流捕获中断事件 → emit approval_required 事件
  4. 前端展示确认弹窗 → 用户点击确认/取消
  5. 前端调用 POST /api/v1/chat/resume → 图恢复执行
"""

from typing import Any

# 需要审批的高风险工具及审批消息模板
_APPROVAL_TOOLS: dict[str, str] = {
    "add_to_watchlist": "将 {company_name} 加入监控清单",
    "remove_from_watchlist": "将 {company_name} 移出监控清单",
    "select_sourcing_result": "确认寻源结果操作: {action}",
}

# manage_scheduled_report 中需要审批的 action
_APPROVAL_SCHEDULED_ACTIONS = {"create", "delete"}

# select_sourcing_result 中需要审批的 action
_APPROVAL_SOURCING_ACTIONS = {"apply_access"}


def needs_approval(tool_name: str, tool_args: dict[str, Any]) -> bool:
    """判断工具调用是否需要用户审批。

    Args:
        tool_name: 工具名称
        tool_args: 工具参数字典

    Returns:
        True 表示需要审批
    """
    if tool_name == "manage_scheduled_report":
        action = tool_args.get("action", "")
        return action in _APPROVAL_SCHEDULED_ACTIONS

    if tool_name == "select_sourcing_result":
        action = tool_args.get("action", "")
        return action in _APPROVAL_SOURCING_ACTIONS

    return tool_name in _APPROVAL_TOOLS


def format_approval_message(tool_name: str, tool_args: dict[str, Any]) -> str:
    """生成用户可见的审批提示消息。

    Args:
        tool_name: 工具名称
        tool_args: 工具参数字典

    Returns:
        中文审批提示消息
    """
    template = _APPROVAL_TOOLS.get(tool_name, "")
    if template:
        try:
            return template.format(**tool_args)
        except KeyError:
            pass

    # 兜底：工具名 + 参数
    args_str = ", ".join(f"{k}={v}" for k, v in tool_args.items())
    return f"执行 {tool_name}({args_str})"


def request_approval(tool_name: str, tool_args: dict[str, Any]) -> bool:
    """在工具执行前请求用户审批。

    调用 LangGraph 的 interrupt() 暂停图执行，等待用户确认。

    Args:
        tool_name: 工具名称
        tool_args: 工具参数字典

    Returns:
        True 表示用户批准，False 表示用户拒绝

    Raises:
        RuntimeError: 不在 LangGraph 图上下文中时调用
    """
    from langgraph.types import interrupt as lg_interrupt

    message = format_approval_message(tool_name, tool_args)

    # interrupt() 暂停图，将消息暴露给外层流处理器
    # 用户确认后，resume 值会在这里返回
    decision = lg_interrupt({
        "type": "approval",
        "tool": tool_name,
        "args": tool_args,
        "message": f"确认操作：{message}？",
    })

    # decision 是 Command(resume=...) 中传入的值
    if isinstance(decision, dict):
        return bool(decision.get("approved", False))
    return bool(decision)


def request_supervisor_approval(
    pending_approvals: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pause a Supervisor run and normalize its aggregate human decision.

    The existing tool approval payload shape is retained so current SSE clients
    can render the interrupt.  An expired decision always fails closed even if
    a stale client also sends ``approved=true``.
    """
    from langgraph.types import interrupt as lg_interrupt

    decision = lg_interrupt(
        {
            "type": "approval",
            "tool": "agent_supervisor",
            "args": {"pending_approvals": pending_approvals},
            "message": "确认执行待审批的供应商操作？",
            "pending_approvals": pending_approvals,
        }
    )
    if not isinstance(decision, dict):
        approved = decision is True
        return {
            "approved": approved,
            "status": "approved" if approved else "rejected",
        }

    status = str(decision.get("status") or "").lower()
    if status in {"expired", "rejected"}:
        approved = False
        normalized_status = status
    else:
        approved = decision.get("approved") is True
        normalized_status = "approved" if approved else "rejected"
    return {**decision, "approved": approved, "status": normalized_status}
