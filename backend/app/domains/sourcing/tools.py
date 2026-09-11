"""寻源工具。"""
from langchain_core.tools import tool


@tool
def list_formal_suppliers(limit: int = 20) -> dict:
    """列出当前正式供应商目录。

    当用户询问“有哪些正式供应商”“已准入供应商清单”或供应商数量时使用。
    该工具只读取当前正式供应商主数据，不创建寻源请求，也不按品类过滤。
    """
    from app.domains.sourcing.supplier_repo import list_formal_suppliers as _list
    from app.tools.executor import get_active_tool_context

    context = get_active_tool_context()
    scoped_call = context is not None
    if scoped_call and not context.user_id:
        return {"status": "denied", "message": "当前会话缺少用户身份，无法读取正式供应商目录"}
    user_id = context.user_id if context else None
    user_role = None
    if user_id:
        from app.domains.auth.service import get_user_by_id

        user = get_user_by_id(user_id)
        user_role = user.role.value if user else None
    if scoped_call:
        from app.domains.supplier.access import list_assigned_supplier_ids

        allowed_ids = list_assigned_supplier_ids(user_id, user_role or "purchaser")
        if user_role != "admin" and not allowed_ids:
            return {"status": "not_found", "count": 0, "items": [], "message": "当前责任范围内暂无正式供应商"}
        result = _list(limit=limit)
        if user_role != "admin":
            result["items"] = [
                item for item in result.get("items", [])
                if str(item.get("supplier_id") or "") in allowed_ids
            ]
            result["total"] = len(result["items"])
        result["scope"] = "当前用户责任范围"
    else:
        result = _list(limit=limit)

    from app.tools.evidence import attach_tool_evidence
    if result.get("total", 0) == 0:
        result["status"] = "not_found"
        return attach_tool_evidence(
            result,
            tool_name="list_formal_suppliers",
            entity_id="sourcing",
            dimension="sourcing",
        )

    from app.graphs.agent_core.evidence_ledger import build_evidence_record

    evidence_records: list[dict] = [build_evidence_record(
        evidence_id="list_formal_suppliers:directory",
        entity_id="sourcing",
        dimension="sourcing",
        provider="formal_supplier_directory",
        source_type="feishu_formal_supplier_snapshot",
        payload={
            "total": result.get("total", 0),
            "items": result.get("items", []),
        },
    ).model_dump(mode="json")]
    claims: list[dict] = []
    for index, supplier in enumerate(result.get("items", [])):
        if not isinstance(supplier, dict) or not supplier.get("supplier_name"):
            continue
        supplier_name = str(supplier["supplier_name"])
        evidence_id = (
            f"list_formal_suppliers:{supplier.get('supplier_id') or supplier_name}:{index}"
        )
        evidence_records.append(build_evidence_record(
            evidence_id=evidence_id,
            entity_id=str(supplier.get("supplier_id") or f"entity:{supplier_name}"),
            dimension="sourcing",
            provider=str(supplier.get("source") or "formal_supplier_directory"),
            source_type="feishu_formal_supplier_snapshot",
            payload=supplier,
        ).model_dump(mode="json"))
        claims.append({
            "claim_id": f"{evidence_id}:claim:supplier_name",
            "entity_id": str(supplier.get("supplier_id") or f"entity:{supplier_name}"),
            "dimension": "sourcing",
            "statement": f"{supplier_name} 是当前正式供应商",
            "value": supplier_name,
            "fact_path": "supplier_name",
            "operator": "eq",
            "evidence_refs": [evidence_id],
            "confidence": 0.99,
        })
    result["evidence_records"] = evidence_records
    result["claims"] = claims
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
def discover_supplier_candidates(requirement: dict) -> dict:
    """按已验证采购需求执行 Harness 只读寻源。

    顺序固定为本地历史/Mongo 与飞书正式快照、天眼查、受限联网；正式
    候选和外部待核验候选分开返回，不创建供应商主数据或准入记录。
    """
    from app.domains.sourcing.service import discover_read_only_sourcing_candidates
    from app.domains.sourcing_risk.discovery_service import discover_candidates
    from app.domains.sourcing_risk.policy_service import resolve_policy_template
    from app.domains.sourcing_risk.requirement_service import SourcingRequirement
    from app.graphs.agent_core.evidence_ledger import build_evidence_record
    from app.tools.evidence import attach_tool_evidence

    validated = SourcingRequirement.model_validate(requirement)
    normalized = validated.model_dump(mode="json", exclude_none=True)
    library_result = discover_read_only_sourcing_candidates(normalized)
    if library_result.get("local_candidates") or library_result.get("external_candidates"):
        result = library_result
        local = [_mark_candidate(item, "historical") for item in result.get("local_candidates", [])]
        external = [_mark_candidate(item, "external") for item in result.get("external_candidates", [])]
    else:
        result = discover_candidates(
            normalized,
            resolve_policy_template(str(normalized.get("category", ""))),
        )
        local = [_mark_candidate(item, "formal") for item in result.get("local_candidates", [])]
        external = [_mark_candidate(item, "external") for item in result.get("external_candidates", [])]
    local = _rank_candidates(local, normalized)
    external = _rank_candidates(external, normalized)
    candidates = [*local, *external]
    evidence_records: list[dict] = []
    claims: list[dict] = []
    for index, candidate in enumerate(candidates):
        name = str(candidate.get("supplier_name") or "").strip()
        if not name:
            continue
        evidence_id = f"sourcing:candidate:{candidate.get('candidate_id') or candidate.get('supplier_id') or index}"
        evidence_records.append(build_evidence_record(
            evidence_id=evidence_id,
            entity_id="sourcing",
            dimension="sourcing",
            provider=str(candidate.get("source") or "sourcing_discovery"),
            source_type=str(candidate.get("source_type") or candidate.get("source") or "unknown"),
            payload={
                "supplier_name": name,
                "candidate_type": candidate.get("candidate_type"),
                "source_stage": candidate.get("source_stage"),
                "source_reference": candidate.get("source_reference"),
                "website_url": candidate.get("website_url"),
                "contact_phone": candidate.get("contact_phone"),
                "contact_email": candidate.get("contact_email"),
            },
        ).model_dump(mode="json"))
        candidate_type = str(candidate.get("candidate_type") or "external")
        if candidate_type == "historical":
            statement = f"{name}存在与当前物料相关的历史供货关系，可作为历史合作候选"
        elif candidate_type == "external":
            statement = f"{name}为外部待核验候选，需确认主体、技术能力与准入条件"
        else:
            statement = f"{name}符合当前寻源条件，可作为正式供应商候选"
        claims.append({
            "claim_id": f"sourcing:recommendation:{candidate.get('candidate_id') or candidate.get('supplier_id') or index}",
            "entity_id": "sourcing",
            "dimension": "sourcing",
            "statement": statement,
            "value": name,
            "fact_path": "supplier_name",
            "operator": "eq",
            "evidence_refs": [evidence_id],
            "confidence": float(candidate.get("identity_confidence") or 0.8),
        })
    payload = {
        "status": "success" if candidates else ("partial" if result.get("external_failure_reasons") else "not_found"),
        "source": result.get("source"),
        "source_order": result.get("source_order", ["local_history", "feishu_formal", "tianyancha", "web_search"]),
        "requirement": normalized,
        "local_candidates": local,
        "external_candidates": external,
        "candidates": candidates,
        "local_status": result.get("local_status"),
        "local_failure_reason": result.get("local_failure_reason"),
        "external_status": result.get("external_status"),
        "external_stop_reason": result.get("external_stop_reason"),
        "external_failure_reasons": result.get("external_failure_reasons", []),
        "external_loop": result.get("external_loop", {}),
        "evidence_records": evidence_records,
        "claims": claims,
        "message": (
            "已返回历史合作候选和盖世外部待核验候选；请先选择企业，再按需发起天眼查核验。"
            if external else "已返回历史合作候选。"
        ) if result is library_result else (
            "外部候选仅为待核验推荐，不会写入供应商主数据。" if external else "已返回正式供应商候选。"
        ),
    }
    return attach_tool_evidence(
        payload,
        tool_name="discover_supplier_candidates",
        entity_id="sourcing",
        dimension="sourcing",
    )


def _mark_candidate(candidate: dict, candidate_type: str) -> dict:
    """Add the stable identity state required by the Harness candidate contract."""
    result = dict(candidate)
    result["candidate_type"] = candidate_type
    if candidate_type == "formal":
        result.setdefault("identity_status", "exact")
        result.setdefault("verification_status", "verified")
        result.setdefault("source_stage", "local_history")
    elif candidate_type == "historical":
        result.setdefault("identity_status", "internal_reference")
        result.setdefault("verification_status", "pending_tianyancha_review")
        result.setdefault("source_stage", "local_history")
    else:
        result.setdefault("verification_status", "pending_verification")
        result.setdefault("identity_status", "pending_verification")
    return result


def _rank_candidates(candidates: list[dict], requirement: dict) -> list[dict]:
    """Rank candidates with inspectable sourcing-only components."""
    ranked: list[dict] = []
    for candidate in candidates:
        match_score = _bounded_score(candidate.get("match_score"), 0.5)
        categories = [str(item) for item in candidate.get("categories", []) if item]
        specifications = [str(item) for item in candidate.get("specifications", []) if item]
        requested_category = str(requirement.get("category") or "")
        requested_specification = str(requirement.get("specification") or "")
        capability_score = sum([
            bool(requested_category and _contains_text(categories, requested_category)),
            bool(requested_specification and _contains_text(specifications, requested_specification)),
        ]) / max(1, sum(bool(item) for item in (requested_category, requested_specification)))
        identity_score = 1.0 if candidate.get("candidate_type") == "formal" else _bounded_score(candidate.get("identity_confidence"), 0.0)
        completeness_score = sum(bool(candidate.get(field)) for field in (
            "source_reference", "source_updated_at", "website_url", "contact_phone", "contact_email"
        )) / 5
        final_rank = round(
            0.55 * match_score + 0.20 * capability_score + 0.15 * identity_score + 0.10 * completeness_score,
            3,
        )
        item = dict(candidate)
        item["ranking_components"] = {
            "requirement_match": round(match_score, 3),
            "capability": round(capability_score, 3),
            "identity_trust": round(identity_score, 3),
            "data_completeness": round(completeness_score, 3),
        }
        item["final_rank"] = final_rank
        item["match_reason"] = "、".join(str(value) for value in candidate.get("match_reasons", []) if value)
        ranked.append(item)
    return sorted(ranked, key=lambda item: item["final_rank"], reverse=True)


def _bounded_score(value: object, default: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, score))


def _contains_text(values: list[str], requested: str) -> bool:
    normalized = "".join(requested.casefold().split())
    return any(normalized and normalized in "".join(value.casefold().split()) for value in values)


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
