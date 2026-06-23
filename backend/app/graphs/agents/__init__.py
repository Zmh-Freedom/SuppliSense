"""LangGraph sub-agent graphs for Multi-Agent supervisor mode."""

from langchain_openai import ChatOpenAI

from app.graphs import build_shared_llm
from app.core.config import settings


def build_domain_llm() -> ChatOpenAI:
    """构建领域 Agent 使用的 LLM 实例。"""
    return build_shared_llm()
