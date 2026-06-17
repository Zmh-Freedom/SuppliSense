"""LangGraph sub-agent graphs for Multi-Agent supervisor mode."""

import os

from langchain_openai import ChatOpenAI


def build_domain_llm() -> ChatOpenAI:
    """构建领域 Agent 使用的 LLM 实例。"""
    return ChatOpenAI(
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        api_key=os.getenv("LLM_API_KEY", ""),
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        temperature=0,
    )
