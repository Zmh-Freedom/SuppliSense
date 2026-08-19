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
    """先检索本地历史供应商，再在候选不足时并行发现天眼查和联网候选。

    外部候选只会以 staged_external 返回，不会自动写入供应商主库。

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
def select_external_supplier_candidate(
    candidate_id: str = "",
    supplier_name: str = "",
    action: str = "apply_access",
) -> dict:
    """对联网待核验候选执行人工确认后的动作。

    可使用 discover_web_suppliers 返回的 candidate_id，或使用候选展示的供应商全称。
    只有天眼查唯一身份核验为 exact 的候选，且用户确认审批后，才会创建准入申请。
    """
    from app.domains.sourcing.repo import get_external_candidate, get_external_candidate_by_name
    from app.graphs.approval import needs_approval, request_approval

    candidate = get_external_candidate(candidate_id) if candidate_id else None
    if not candidate and supplier_name.strip():
        candidate = get_external_candidate_by_name(supplier_name)
        candidate_id = str(candidate.get("_id", "")) if candidate else ""
    if not candidate:
        return {"success": False, "error": "external_candidate_not_found", "message": "未找到对应的联网候选，请提供供应商全称"}

    args = {
        "candidate_id": candidate_id,
        "supplier_name": candidate.get("supplier_name", supplier_name),
        "action": action,
    }
    if needs_approval("select_external_supplier_candidate", args):
        try:
            approved = request_approval("select_external_supplier_candidate", args)
        except RuntimeError:
            return {
                "success": False,
                "error": "approval_context_required",
                "message": "外部候选准入必须在支持人工审批的 Agent 会话中执行",
            }
        if not approved:
            return {"cancelled": True, "message": "用户取消了外部候选准入申请操作"}

    from app.domains.sourcing.service import select_external_candidate
    return select_external_candidate(candidate_id, action, user_id="agent")


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


@tool
def discover_web_suppliers(category: str, specification: str = "", region: str = "") -> dict:
    """联网发现供应商候选，只返回待核验结果，不写入供应商主库。

    Args:
        category: 采购品类，如“钢材”或“不锈钢板”。
        specification: 规格或产品关键词。
        region: 期望供应商地区。
    """
    from app.domains.sourcing_risk.discovery_service import search_external_provider, stage_external_candidates

    requirement = {"category": category, "specification": specification, "region": region}
    candidates = search_external_provider(requirement)
    staged = stage_external_candidates("", candidates)
    from app.domains.sourcing.repo import save_external_candidate
    for candidate in staged:
        save_external_candidate(candidate)
    return {
        "source": "public_web_search",
        "status": "staged_external",
        "candidates": staged,
        "message": "联网结果仅为待核验候选，确认前不会写入供应商主库。",
    }
