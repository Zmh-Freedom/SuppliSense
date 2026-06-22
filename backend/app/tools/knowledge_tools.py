"""知识库工具。"""
from langchain_core.tools import tool


@tool
def knowledge_search(query: str, company_name: str = "") -> dict:
    """从知识库检索相关文档（财报、合同、ESG报告等）。

    Args:
        query: 检索关键词
        company_name: 企业全称（可选）
    """
    from app.services.retriever import retrieve_context
    context = retrieve_context(query, n_results=5)
    if not context:
        return {"message": "未找到相关文档", "suggestion": "请先上传相关文档到知识库"}
    return {
        "query": query,
        "company_name": company_name,
        "context": context,
        "message": "以下是从知识库检索到的相关信息",
    }
