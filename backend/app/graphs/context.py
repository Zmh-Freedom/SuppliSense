"""对话上下文管理 — 长对话摘要压缩。"""

from langchain_core.messages import SystemMessage
from app.graphs import build_shared_llm

from app.core.logging import get_logger

logger = get_logger()

SUMMARY_THRESHOLD = 16  # 超过 16 条消息（8 轮）时触发摘要
KEEP_RECENT = 8  # 保留最近 8 条消息（4 轮）


async def _llm_summarize(messages: list[dict]) -> str:
    """用 LLM 对对话历史生成摘要。"""
    llm = build_shared_llm()

    history_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in messages
    )

    prompt = (
        "请用中文简洁总结以下对话的要点（100字以内），"
        "保留关键的企业名称、风险指标和分析结论：\n\n"
        f"{history_text}"
    )

    try:
        response = await llm.ainvoke([SystemMessage(content=prompt)])
        return response.content.strip()
    except Exception as e:
        logger.error("summarize_failed", error=str(e))
        return ""


async def build_context_messages(history: list[dict]) -> list[dict]:
    """构建上下文消息，长对话时自动摘要压缩。

    - 消息数 <= SUMMARY_THRESHOLD: 返回完整历史
    - 消息数 > SUMMARY_THRESHOLD: 对前半部分生成摘要，保留最近 KEEP_RECENT 条
    """
    if len(history) <= SUMMARY_THRESHOLD:
        return history

    # 对前半部分生成摘要
    early = history[:-KEEP_RECENT]
    summary = await _llm_summarize(early)

    if not summary:
        # 摘要失败，直接截断
        return history[-KEEP_RECENT:]

    return [
        {"role": "system", "content": f"对话历史摘要：{summary}"},
        *history[-KEEP_RECENT:],
    ]


async def build_input_messages(
    history: list[dict],
    user_message: str,
    references: list[dict] | None = None,
) -> list:
    """构建 LangChain 输入消息列表，长对话自动摘要。

    所有图的 stream 函数统一使用此 helper，确保上下文窗口管理一致。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    input_messages: list = []
    if references:
        from app.services.conversation_state import build_conversation_state

        names = [
            str(reference.get("name"))
            for reference in references
            if isinstance(reference, dict) and reference.get("name")
        ]
        if names:
            conversation_state = build_conversation_state(user_message, references)
            selected = conversation_state["selected_suppliers"]
            dimensions = conversation_state["current_task"]["analysis_dimensions"]
            target_hint = (
                "本轮已解析的目标供应商：" + "、".join(selected) + "。"
                if selected else "本轮未出现可确定的供应商指代。"
            )
            dimension_hint = (
                "本轮分析维度：" + "、".join(dimensions) + "。"
                if dimensions else "本轮未指定分析维度。"
            )
            input_messages.append(
                SystemMessage(
                    content=(
                        "当前会话已识别的供应商实体（后续‘它/这家供应商’默认优先指向这些实体）："
                        + "、".join(dict.fromkeys(names))
                        + "。用户说‘这些企业/上述企业/它们/推荐的供应商’时，"
                        "应将全部上述实体作为分析对象并逐家调用所需工具；"
                        "若用户明确提到其他企业，以用户当前表述为准。"
                        + target_hint + dimension_hint
                    )
                )
            )
    if history:
        context = await build_context_messages(history)
        for m in context:
            if m.get("role") == "system":
                input_messages.append(SystemMessage(content=m["content"]))
            elif m.get("role") == "user":
                input_messages.append(HumanMessage(content=m["content"]))
            else:
                input_messages.append({"role": m["role"], "content": m["content"]})
    input_messages.append(HumanMessage(content=user_message))
    return input_messages
