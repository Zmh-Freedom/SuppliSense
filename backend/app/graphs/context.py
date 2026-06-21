"""对话上下文管理 — 长对话摘要压缩。"""

import asyncio
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger()

SUMMARY_THRESHOLD = 16  # 超过 16 条消息（8 轮）时触发摘要
KEEP_RECENT = 8  # 保留最近 8 条消息（4 轮）


async def _llm_summarize(messages: list[dict]) -> str:
    """用 LLM 对对话历史生成摘要。"""
    llm = ChatOpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        temperature=0,
    )

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


async def build_input_messages(history: list[dict], user_message: str) -> list:
    """构建 LangChain 输入消息列表，长对话自动摘要。

    所有图的 stream 函数统一使用此 helper，确保上下文窗口管理一致。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    input_messages: list = []
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
