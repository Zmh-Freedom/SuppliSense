"""预警监控工具。"""
from langchain_core.tools import tool


def _active_scope() -> tuple[str | None, str | None, bool]:
    """Return the current user scope without changing direct tool compatibility."""
    from app.tools.executor import get_active_tool_context

    context = get_active_tool_context()
    if context is None:
        return None, None, False
    user_id = context.user_id
    if not user_id:
        return None, None, True
    from app.domains.auth.service import get_user_by_id

    user = get_user_by_id(user_id)
    return user_id, user.role.value if user else None, True


@tool
def investigate_supplier_monitoring(query: str) -> dict:
    """调查一个供应商是否适合加入风险监控。

    自动查询主体候选、内部月度交易、历史零件合作、可用公开财务和
    天眼查资料覆盖。只读，不会加入监控；用户确认后才应调用加入监控。
    """
    from app.domains.alert.intake_service import investigate_supplier_monitoring as _investigate
    from app.tools.evidence import attach_tool_evidence

    result = _investigate(query)
    return attach_tool_evidence(
        result,
        tool_name="investigate_supplier_monitoring",
        entity_id=f"investigation:{query}",
        dimension="risk_monitoring",
    )


@tool
def check_alert(
    company_name: str,
    monitor_target_id: str | None = None,
) -> dict:
    """查看企业预警变化。

    Args:
        company_name: 企业全称
    """
    from app.domains.alert.service import detect_changes, get_latest_snapshot
    if not get_latest_snapshot(company_name, monitor_target_id=monitor_target_id):
        return {"status": "not_found", "company_name": company_name, "message": "暂无历史快照"}
    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(
        detect_changes(company_name, monitor_target_id=monitor_target_id),
        tool_name="check_alert",
        entity_id=monitor_target_id or f"entity:{company_name}",
        dimension="risk_monitoring",
    )


@tool
def get_watchlist() -> dict:
    """获取监控清单。"""
    from app.domains.alert.service import get_watchlist as _get_watchlist
    from app.domains.alert.service import get_watchlist_targets

    user_id, user_role, scoped_call = _active_scope()
    if scoped_call and not user_id:
        return {"status": "denied", "message": "当前会话缺少用户身份，无法读取监控清单"}
    companies = _get_watchlist() if not scoped_call else None
    targets = get_watchlist_targets(user_id, user_role) if scoped_call else get_watchlist_targets()
    companies = companies if companies is not None else [
        target.get("company_name") for target in targets if target.get("company_name")
    ]
    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(
        {
            "count": len(companies),
            "companies": companies,
            "targets": targets,
            "scope": "当前用户责任范围" if scoped_call else "系统监控清单",
            "claims": [{
                "claim_id": "watchlist:count",
                "entity_id": "watchlist",
                "dimension": "risk_monitoring",
                "statement": f"当前可见监控对象共 {len(companies)} 家",
                "value": len(companies),
                "fact_path": "count",
                "operator": "eq",
                "evidence_refs": ["get_watchlist:watchlist:risk_monitoring"],
                "confidence": 0.99,
            }, *[
                {
                    "claim_id": f"watchlist:item:{index}",
                    "entity_id": "watchlist",
                    "dimension": "risk_monitoring",
                    "statement": f"监控对象：{name}",
                    "value": name,
                    "operator": "eq",
                    "evidence_refs": ["get_watchlist:watchlist:risk_monitoring"],
                    "confidence": 0.99,
                }
                for index, name in enumerate(companies)
                if name
            ]],
        },
        tool_name="get_watchlist",
        entity_id="watchlist",
        dimension="risk_monitoring",
    )


@tool
def analyze_watchlist_trend(period_months: int = 1) -> dict:
    """分析监控清单中所有企业的风险变化趋势。

    一次性获取监控清单 + 全部企业的趋势数据，避免多次工具调用。
    用户询问"监控清单趋势"、"本月风险变化"等场景使用。

    Args:
        period_months: 分析周期（月），默认 1 表示本月
    """
    from datetime import datetime, timedelta, timezone
    from app.db.mongo import get_db
    from app.domains.alert.service import get_watchlist_targets

    user_id, user_role, scoped_call = _active_scope()
    if scoped_call and not user_id:
        return {"status": "denied", "message": "当前会话缺少用户身份，无法分析监控清单趋势"}
    targets = get_watchlist_targets(user_id, user_role) if scoped_call else get_watchlist_targets()
    if not targets:
        return {"status": "not_found", "count": 0, "message": "监控清单为空", "companies": []}

    db = get_db()
    days = period_months * 30
    since = datetime.now(timezone.utc) - timedelta(days=days)
    results = []

    for target in targets:
        name = target.get("company_name", "")
        monitor_target_id = target.get("monitor_target_id")
        snapshots = list(
            db["alert_snapshots"]
            .find(
                {"$or": [
                    {"monitor_target_id": monitor_target_id},
                    {"company_name": name},
                ], "checked_at": {"$gte": since}},
                {"checked_at": 1, "risk_score": 1, "risk_level": 1, "_id": 0},
            )
            .sort("checked_at", 1)
        )
        data = [
            {"date": s["checked_at"].strftime("%Y-%m-%d"), "risk_score": s.get("risk_score", 0), "risk_level": s.get("risk_level", "")}
            for s in snapshots
        ]
        trend = "暂无数据"
        if len(data) == 1:
            trend = "数据不足"
        elif len(data) >= 2:
            delta = data[-1]["risk_score"] - data[0]["risk_score"]
            trend = "恶化" if delta > 10 else "改善" if delta < -10 else "稳定"

        results.append({
            "company_name": name,
            "monitor_target_id": monitor_target_id,
            "target_type": target.get("target_type"),
            "supplier_id": target.get("supplier_id"),
            "candidate_id": target.get("candidate_id"),
            "company_id": target.get("company_id"),
            "trend": trend,
            "trend_data_points": len(data),
            "latest_score": data[-1]["risk_score"] if data else None,
            "latest_level": data[-1]["risk_level"] if data else None,
            "data": data,
        })

    from app.tools.evidence import attach_tool_evidence

    payload = {"count": len(results), "period_months": period_months, "companies": results, "scope": "当前用户责任范围" if scoped_call else "系统监控清单"}
    evidence_id = "analyze_watchlist_trend:watchlist:risk_monitoring"
    payload["claims"] = [{
        "claim_id": f"{evidence_id}:claim:count",
        "entity_id": "watchlist",
        "dimension": "risk_monitoring",
        "statement": f"当前可见监控对象中有 {len(results)} 家纳入本次趋势分析",
        "value": len(results),
        "fact_path": "count",
        "operator": "eq",
        "evidence_refs": [evidence_id],
        "confidence": 0.99,
    }]
    for item in results:
        if not item.get("company_name") or not item.get("trend"):
            continue
        payload["claims"].append({
            "claim_id": f"{evidence_id}:claim:{item['monitor_target_id'] or item['company_name']}",
            "entity_id": "watchlist",
            "dimension": "risk_monitoring",
            "statement": f"{item['company_name']} 最近 {period_months} 个月风险变化：{item['trend']}（{item['trend_data_points']} 个数据点）",
            "value": item["trend"],
            "operator": "eq",
            "evidence_refs": [evidence_id],
            "confidence": 0.9 if item["trend_data_points"] >= 2 else 0.65,
        })
    return attach_tool_evidence(payload, tool_name="analyze_watchlist_trend", entity_id="watchlist", dimension="risk_monitoring")


@tool
def get_monitor_review_queue() -> dict:
    """查看当前采购员或管理员可见的待复核事项。"""
    user_id, user_role, scoped_call = _active_scope()
    if not scoped_call or not user_id:
        return {"status": "denied", "message": "查看待复核事项需要已登录的采购员或管理员身份"}
    from app.domains.alert.review_tasks import list_review_tasks

    tasks = list_review_tasks(None, user_id, user_role or "analyst")
    pending = [
        task for task in tasks
        if str(task.get("status") or "") in {"pending_approval", "approved", "executing", "needs_review"}
    ]
    payload = {
        "status": "success" if pending else "not_found",
        "count": len(pending),
        "tasks": pending,
        "message": "暂无待复核事项" if not pending else f"当前有 {len(pending)} 项待复核事项",
    }
    if pending:
        payload["claims"] = [{
            "claim_id": "review_queue:count",
            "entity_id": "review_queue",
            "dimension": "risk_monitoring",
            "statement": f"当前责任范围内有 {len(pending)} 项待复核事项",
            "value": len(pending),
            "fact_path": "count",
            "operator": "eq",
            "evidence_refs": ["get_monitor_review_queue:review_queue:risk_monitoring"],
            "confidence": 0.99,
        }]
    return attach_tool_evidence(payload, tool_name="get_monitor_review_queue", entity_id="review_queue", dimension="risk_monitoring")


@tool
def add_to_watchlist(
    company_name: str = "",
    target_source: str = "conversation_state",
    target_type: str | None = None,
    monitor_target_id: str | None = None,
    supplier_id: str | None = None,
    candidate_id: str | None = None,
    company_id: str | None = None,
    supplier_code: str | None = None,
) -> dict:
    """将企业加入监控清单（需要用户确认）。

    Args:
        company_name: 企业全称（兼容字段，优先使用稳定 ID）
        target_source: 企业引用来源，写操作必须来自当前会话上下文
        target_type: formal_supplier / external_candidate / company
        monitor_target_id: 已存在的监控对象 ID
        supplier_id: 正式供应商稳定 ID
        candidate_id: 外部候选稳定 ID
        company_id: 法定企业稳定 ID
    """
    del target_source
    from app.graphs.approval import needs_approval, request_approval
    from app.tools.executor import get_active_tool_context

    active_context = get_active_tool_context()
    approval_args = {
        "company_name": company_name,
        "target_type": target_type,
        "monitor_target_id": monitor_target_id,
        "supplier_id": supplier_id,
        "candidate_id": candidate_id,
        "company_id": company_id,
    }
    if not active_context and needs_approval("add_to_watchlist", approval_args):
        try:
            approved = request_approval("add_to_watchlist", approval_args)
        except RuntimeError:
            return {"success": False, "error": "approval_context_required", "message": "加入监控必须在支持人工审批的 Agent 会话中执行"}
        if not approved:
            return {"cancelled": True, "message": f"用户取消了将 {company_name} 加入监控清单的操作"}

    from app.domains.alert.service import add_to_watchlist as _add
    result = _add(
        company_name,
        target_type=target_type,
        monitor_target_id=monitor_target_id,
        supplier_id=supplier_id,
        candidate_id=candidate_id,
        company_id=company_id,
        supplier_code=supplier_code,
    )
    return {
        **result,
        "side_effect_receipt": {
            "receipt_id": active_context.idempotency_key if active_context else f"watchlist:add:{company_name}",
            "operation": "add_to_watchlist",
            "company_name": result.get("company_name") or company_name,
            "monitor_target_id": result.get("monitor_target_id") or monitor_target_id,
            "supplier_id": result.get("supplier_id") or supplier_id,
            "candidate_id": result.get("candidate_id") or candidate_id,
            "company_id": result.get("company_id") or company_id,
        },
    }


@tool
def remove_from_watchlist(
    company_name: str = "",
    target_source: str = "conversation_state",
    monitor_target_id: str | None = None,
    supplier_id: str | None = None,
    candidate_id: str | None = None,
    company_id: str | None = None,
) -> dict:
    """将企业从监控清单移除（需要用户确认）。

    Args:
        company_name: 企业全称（兼容字段，优先使用稳定 ID）
        target_source: 企业引用来源，写操作必须来自当前会话上下文
    """
    del target_source
    from app.graphs.approval import needs_approval, request_approval
    from app.tools.executor import get_active_tool_context

    active_context = get_active_tool_context()
    approval_args = {
        "company_name": company_name,
        "monitor_target_id": monitor_target_id,
        "supplier_id": supplier_id,
        "candidate_id": candidate_id,
        "company_id": company_id,
    }
    if not active_context and needs_approval("remove_from_watchlist", approval_args):
        try:
            approved = request_approval("remove_from_watchlist", approval_args)
        except RuntimeError:
            return {"success": False, "error": "approval_context_required", "message": "移出监控必须在支持人工审批的 Agent 会话中执行"}
        if not approved:
            return {"cancelled": True, "message": f"用户取消了将 {company_name} 移出监控清单的操作"}

    from app.domains.alert.service import remove_from_watchlist as _remove
    result = _remove(
        company_name,
        monitor_target_id=monitor_target_id,
        supplier_id=supplier_id,
        candidate_id=candidate_id,
        company_id=company_id,
    )
    return {
        **result,
        "side_effect_receipt": {
            "receipt_id": active_context.idempotency_key if active_context else f"watchlist:remove:{company_name}",
            "operation": "remove_from_watchlist",
            "company_name": result.get("company_name") or company_name,
            "monitor_target_id": result.get("monitor_target_id") or monitor_target_id,
            "supplier_id": result.get("supplier_id") or supplier_id,
            "candidate_id": result.get("candidate_id") or candidate_id,
            "company_id": result.get("company_id") or company_id,
        },
    }
