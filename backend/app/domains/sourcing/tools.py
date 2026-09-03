"""寻源工具。"""
from langchain_core.tools import tool


@tool
def list_formal_suppliers(limit: int = 20) -> dict:
    """列出当前正式供应商目录。

    当用户询问“有哪些正式供应商”“已准入供应商清单”或供应商数量时使用。
    该工具只读取当前正式供应商主数据，不创建寻源请求，也不按品类过滤。
    """
    from app.domains.sourcing.supplier_repo import list_formal_suppliers as _list

    from app.tools.evidence import attach_tool_evidence

    result = _list(limit=limit)
    if result.get("total", 0) == 0:
        result["status"] = "not_found"
    return attach_tool_evidence(result, tool_name="list_formal_suppliers", entity_id="sourcing", dimension="sourcing")


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
    from app.tools.evidence import attach_tool_evidence

    result = _search(request_id)
    if not result.get("results") and not result.get("external_candidates"):
        result["status"] = "not_found"
    return attach_tool_evidence(result, tool_name="search_suppliers", entity_id="sourcing", dimension="sourcing")


@tool
def select_sourcing_result(result_id: str, action: str = "watchlist") -> dict:
    """将正式供应商候选加入风险监控列表。

    Args:
        result_id: 寻源结果 ID
        action: 当前仅支持 watchlist（加入监控，需人工确认）。
    """
    if action != "watchlist":
        return {
            "success": False,
            "error": "scope_restricted",
            "message": "当前阶段不执行供应商准入，请在供应商管理系统中完成；本 Agent 仅支持推荐和加入风险监控。",
        }
    from app.graphs.approval import needs_approval, request_approval

    if needs_approval("select_sourcing_result", {"result_id": result_id, "action": action}):
        try:
            approved = request_approval("select_sourcing_result", {"result_id": result_id, "action": action})
        except RuntimeError:
            return {
                "success": False,
                "error": "approval_context_required",
                "message": "准入申请必须在支持人工审批的 Agent 会话中执行",
            }
        if not approved:
            return {"cancelled": True, "message": f"用户取消了准入申请操作"}

    from app.domains.sourcing.service import select_result as _select
    return _select(result_id, action, user_id="agent")


@tool
def select_external_supplier_candidate(
    candidate_id: str = "",
    supplier_name: str = "",
    action: str = "watchlist",
) -> dict:
    """对联网待核验候选执行人工确认后的监控动作。

    可使用 discover_web_suppliers 返回的 candidate_id，或使用候选展示的供应商全称。
    外部候选不会被导入正式供应商主数据；用户确认后仅可加入风险监控。
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
    if action != "watchlist":
        return {
            "success": False,
            "error": "scope_restricted",
            "message": "当前阶段不执行供应商准入，请在供应商管理系统中完成；本 Agent 仅支持推荐和加入风险监控。",
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
    """当前范围禁用自动扩充供应商主库。

    Args:
        keyword: 搜索关键词，如 "电机制造"、"伺服电机"、"包装印刷"
        industry: 行业分类，如 "电气机械和器材制造业"、"软件和信息技术服务业"
        region: 地域，如 "浙江"、"广东"、"华东"
    """
    del keyword, industry, region
    return {
        "success": False,
        "error": "scope_restricted",
        "message": "当前 Agent 只支持供应商推荐和加入风险监控，不自动写入供应商主数据；请在供应商管理系统完成扩库。",
    }


@tool
def discover_web_suppliers(category: str, specification: str = "", region: str = "") -> dict:
    """联网发现供应商候选，只返回待核验结果，不写入供应商主库。

    Args:
        category: 采购品类，如“钢材”或“不锈钢板”。
        specification: 规格或产品关键词。
        region: 期望供应商地区。
    """
    from app.domains.sourcing_risk.discovery_service import discover_external_provider, stage_external_candidates

    requirement = {"category": category, "specification": specification, "region": region}
    discovery = discover_external_provider(requirement)
    staged = stage_external_candidates("", discovery.get("candidates", []))
    from app.domains.sourcing.repo import save_external_candidate
    for candidate in staged:
        save_external_candidate(candidate)
    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence({
        "source": "public_web_search",
        "status": discovery.get("status", "not_found"),
        "candidates": staged,
        "failed_stages": discovery.get("failed_stages", []),
        "failure_reasons": discovery.get("failure_reasons", []),
        "message": "联网结果仅为待核验候选，确认前不会写入供应商主库。",
    }, tool_name="discover_web_suppliers", entity_id="sourcing", dimension="sourcing", source_type="public_web_search")
