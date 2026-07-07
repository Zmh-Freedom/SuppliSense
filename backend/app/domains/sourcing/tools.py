"""寻源工具。"""
from langchain_core.tools import tool


@tool
def create_sourcing_request(title: str, category: str, spec: str) -> dict:
    """创建采购寻源请求，后续可通过 search_suppliers 执行搜索。

    Args:
        title: 需求标题，如"摄像头采购"
        category: 采购品类，如"安防设备"
        spec: 规格/技术要求描述
    """
    from app.schemas.sourcing import SourcingRequestInput
    from app.domains.sourcing.service import create_sourcing_request as _create

    rid = _create(SourcingRequestInput(title=title, category=category, spec=spec), user_id="agent")
    return {"request_id": rid, "status": "created"}


@tool
def search_suppliers(request_id: str) -> dict:
    """执行供应商搜索和风险评估排序，返回 Top-10 候选供应商结果。

    Args:
        request_id: 寻源请求 ID（由 create_sourcing_request 返回）
    """
    from app.domains.sourcing.service import search_suppliers as _search
    return _search(request_id)


@tool
def select_sourcing_result(result_id: str, action: str = "watchlist") -> dict:
    """勾选寻源结果执行动作：加入监控列表或申请准入。

    Args:
        result_id: 寻源结果 ID
        action: 动作类型，可选值: watchlist(加入监控), apply_access(申请准入，需确认)
    """
    from app.graphs.approval import needs_approval, request_approval

    if needs_approval("select_sourcing_result", {"result_id": result_id, "action": action}):
        try:
            approved = request_approval("select_sourcing_result", {"result_id": result_id, "action": action})
        except RuntimeError:
            approved = True
        if not approved:
            return {"cancelled": True, "message": f"用户取消了准入申请操作"}

    from app.domains.sourcing.service import select_result as _select
    return _select(result_id, action, user_id="agent")


@tool
def expand_supplier_library(keyword: str = "", industry: str = "", region: str = "") -> dict:
    """从天眼查搜索企业并自动导入供应商主库。当用户要寻找某类供应商但本地库找不到时使用。

    Args:
        keyword: 搜索关键词，如 "电机制造"、"伺服电机"、"包装印刷"
        industry: 行业分类，如 "电气机械和器材制造业"、"软件和信息技术服务业"
        region: 地域，如 "浙江"、"广东"、"华东"
    """
    from app.domains.sourcing.import_service import import_from_tianyancha_search
    return import_from_tianyancha_search(keyword=keyword, industry=industry, region=region, max_results=50)
