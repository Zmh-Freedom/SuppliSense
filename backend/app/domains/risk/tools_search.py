"""数据查询工具。"""
from langchain_core.tools import tool


@tool
def search_company(keyword: str) -> dict:
    """根据关键词搜索企业全称，返回匹配的企业名称列表。

    Args:
        keyword: 企业名称关键词，如'海康'、'大华'
    """
    from app.domains.risk.repo_company import search_companies
    from app.domains.risk.repo_financial import resolve_full_name
    from app.services.tianyancha_client import fetch_company

    results = search_companies(keyword)
    if not results:
        try:
            fetch_company(keyword)
        except Exception:
            pass
        results = search_companies(keyword)

    if not results:
        full_name = resolve_full_name(keyword)
        if full_name and full_name != keyword:
            try:
                fetch_company(full_name)
            except Exception:
                pass
            results = search_companies(keyword)
            if not results:
                results = search_companies(full_name)

    return {"keyword": keyword, "count": len(results), "results": results}


@tool
def tianyancha_query(endpoint: str, keyword: str) -> dict:
    """调用天眼查 API 查询企业数据。可用的 endpoint 和对应功能：

    Args:
        endpoint: 天眼查 API 完整路径
        keyword: 企业名称关键词
    """
    from app.services.tianyancha_client import query

    result = query(endpoint, keyword)
    if result is None:
        return {"error": "API 调用失败", "endpoint": endpoint, "keyword": keyword}
    return {"endpoint": endpoint, "keyword": keyword, "data": result}
