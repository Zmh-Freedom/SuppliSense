"""LangGraph sub-agent graphs for Multi-Agent supervisor mode."""

from langchain_openai import ChatOpenAI
from app.core.config import settings


def build_domain_llm() -> ChatOpenAI:
    """构建领域 Agent 使用的 LLM 实例。"""
    return ChatOpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        temperature=0,
    )
