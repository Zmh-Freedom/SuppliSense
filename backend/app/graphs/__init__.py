"""LangGraph agent graphs for SuppliSense."""

import os
from langchain_openai import ChatOpenAI

from app.core.config import settings

# Retry configuration
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "10"))


def format_llm_error(e: Exception) -> str:
    """将 LLM API 错误翻译为中文提示。"""
    # Domain validation failures can surface through the same streaming error
    # boundary as provider failures. Keep the user-facing business message
    # instead of mislabeling it as an LLM outage.
    if hasattr(e, "code") and hasattr(e, "message"):
        return str(getattr(e, "message"))
    msg = str(e)
    if "Insufficient Balance" in msg or "402" in msg:
        return "LLM API 余额不足，请联系管理员充值"
    if "Rate limit" in msg or "429" in msg or "rate_limit" in msg:
        return "请求过于频繁，请稍后重试"
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return "LLM 服务响应超时，请重试"
    if "401" in msg or "Unauthorized" in msg or "Authentication" in msg:
        return "LLM API 认证失败，请检查配置"
    if "Connection" in msg or "connect" in msg.lower():
        return "LLM 服务连接失败，请检查网络"
    return f"LLM 服务异常: {msg}"


def build_shared_llm(streaming: bool = False) -> ChatOpenAI:
    """统一 LLM 工厂，所有 graph 共用。

    - 自动重试 3 次
    - 60s 超时
    - temperature=0
    """
    return ChatOpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        temperature=0,
        streaming=streaming,
        max_retries=LLM_MAX_RETRIES,
        timeout=LLM_TIMEOUT,
    )
