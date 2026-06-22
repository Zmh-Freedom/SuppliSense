"""LangGraph agent graphs for SuppliSense."""

import os
from langchain_openai import ChatOpenAI

from app.core.config import settings

# Retry configuration
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "10"))


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
