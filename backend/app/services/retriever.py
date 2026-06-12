"""
Retriever: Retrieve relevant documents from knowledge base for RAG.
"""

from typing import Any

from app.services.knowledge_base import search


def retrieve_context(
    query: str,
    n_results: int = 5,
    filters: dict[str, Any] | None = None,
) -> str:
    """
    Retrieve relevant context from knowledge base.

    Args:
        query: User query or search text
        n_results: Number of results to retrieve
        filters: Optional metadata filters

    Returns:
        Formatted context string for LLM
    """
    results = search(query, n_results=n_results, filters=filters)

    if not results:
        return ""

    # Format results into context string
    context_parts = []
    for i, result in enumerate(results, 1):
        content = result["content"]
        metadata = result.get("metadata", {})
        source = metadata.get("source", "Unknown")
        page = metadata.get("page", "")
        page_str = f" (第{page}页)" if page else ""

        context_parts.append(
            f"[{i}] 来源: {source}{page_str}\n内容: {content}"
        )

    return "\n\n".join(context_parts)


def retrieve_for_agent(
    user_message: str,
    company_name: str | None = None,
) -> str:
    """
    Retrieve context specifically for agent use.

    Args:
        user_message: User's question
        company_name: Optional company name to filter results

    Returns:
        Context string for injection into system prompt
    """
    # Build search query
    query = user_message
    if company_name:
        query = f"{company_name} {user_message}"

    # Build filters
    filters = None
    if company_name:
        # Try to filter by company name in metadata
        filters = {"source": {"$contains": company_name}}

    # Retrieve context
    context = retrieve_context(query, n_results=3, filters=filters)

    if not context:
        # Fallback: search without filters
        context = retrieve_context(query, n_results=3)

    return context


def format_context_for_prompt(context: str) -> str:
    """
    Format retrieved context for injection into LLM prompt.

    Args:
        context: Raw context string from retrieve_context

    Returns:
        Formatted context for system prompt
    """
    if not context:
        return ""

    return f"""
## 知识库检索结果

以下是从知识库中检索到的相关信息，请在回答时参考：

{context}

---
"""
