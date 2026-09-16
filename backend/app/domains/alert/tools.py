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
def resolve_monitor_identity(
    monitor_target_id: str | None = None,
    company_name: str | None = None,
) -> dict:
    """核验监控对象对应的企业主体候选（只读）。

    优先使用稳定的 monitor_target_id；没有该 ID 时才按企业名称检索，
    不会自动绑定主体，也不会修改监控清单。
    """
    from app.domains.alert.service import resolve_watchlist_identity
    from app.domains.alert.intake_service import _external_identity_candidate, _load_external_profile
    from app.domains.company.service import search_identity
    from app.tools.evidence import attach_tool_evidence

    target_id = str(monitor_target_id or "").strip() or None
    query = str(company_name or "").strip()
    try:
        if target_id:
            result = resolve_watchlist_identity(target_id)
        elif query:
            result = {
                "monitor_target_id": None,
                "query": query,
                **search_identity(query, limit=10),
            }
            # Chat主体核验没有 monitor_target_id 时不能走
            # resolve_watchlist_identity，但仍应复用同一份外部主体资料。
            # 否则监控页面能看到天眼查候选，聊天入口却会错误地返回“没有候选”。
            if not result.get("exact") and not result.get("candidates"):
                external_profile, enterprise_state = _load_external_profile(query)
                if external_profile:
                    result.update({
                        "resolution": "candidates",
                        "candidates": [_external_identity_candidate(external_profile, query)],
                        "source": "天眼查工商主体查询",
                        "source_mode": enterprise_state.get("status") if isinstance(enterprise_state, dict) else "available",
                        "external_profile": external_profile,
                    })
        else:
            return {"status": "invalid", "message": "主体核验缺少监控对象 ID 或企业名称"}
    except ValueError as exc:
        return {
            "status": "not_found",
            "monitor_target_id": target_id,
            "query": query,
            "resolution": "not_found",
            "exact": None,
            "candidates": [],
            "message": str(exc),
        }

    resolution = str(result.get("resolution") or "pending_verification")
    exact = result.get("exact")
    candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
    result["limitations"] = [
        "主体核验只返回候选或精确匹配，不会自动绑定监控对象。"
    ]
    if not exact and not candidates:
        result["limitations"].append("当前统一主体库没有可确认的候选，未形成主体身份结论。")
    result["claims"] = [{
        "claim_id": f"resolve_monitor_identity:{target_id or query}:resolution",
        "entity_id": target_id or f"entity:{query}",
        "dimension": "identity_review",
        "statement": (
            f"主体检索结果：已找到 1 个精确主体候选"
            if exact else f"主体检索结果：{len(candidates)} 个候选，等待采购人员确认"
        ),
        "value": resolution,
        "fact_path": "resolution",
        "operator": "eq",
        "evidence_refs": [f"resolve_monitor_identity:{target_id or query}:identity_review"],
        "confidence": 0.95 if exact else 0.85,
    }]
    return attach_tool_evidence(
        result,
        tool_name="resolve_monitor_identity",
        entity_id=target_id or f"entity:{query}",
        dimension="identity_review",
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
            {
                "date": s["checked_at"].strftime("%Y-%m-%d"),
                "risk_score": s.get("risk_score"),
                "risk_level": s.get("risk_level") or "",
            }
            for s in snapshots
        ]
        score_points = [
            item for item in data
            if isinstance(item.get("risk_score"), (int, float))
        ]
        latest = data[-1] if data else {}
        latest_score = latest.get("risk_score")
        latest_level = latest.get("risk_level") or None
        previous_score = score_points[0].get("risk_score") if score_points else None
        delta = (
            latest_score - previous_score
            if isinstance(latest_score, (int, float))
            and isinstance(previous_score, (int, float))
            and len(score_points) >= 2
            else None
        )
        if not data:
            trend = "暂无快照"
        elif len(score_points) < 2:
            trend = "仅有1次评分" if len(score_points) == 1 else "评分缺失"
        else:
            # Safety score decreases when risk deteriorates.
            trend = "恶化" if delta is not None and delta < -10 else "改善" if delta is not None and delta > 10 else "稳定"

        if isinstance(latest_score, (int, float)):
            score_text = f"当前风险评分：{latest_score:g}/100"
        else:
            score_text = "当前风险评分：暂无"
        level_text = f"风险等级：{latest_level}" if latest_level else "风险等级：暂无"
        if delta is not None:
            comparison_text = f"较周期初{'+' if delta >= 0 else ''}{delta:g}分"
        elif not data:
            comparison_text = "本周期没有风险快照"
        elif len(score_points) == 1:
            comparison_text = "仅有1次评分，暂无法判断变化方向"
        else:
            comparison_text = "有快照但评分缺失，暂无法比较"

        results.append({
            "company_name": name,
            "monitor_target_id": monitor_target_id,
            "target_type": target.get("target_type"),
            "supplier_id": target.get("supplier_id"),
            "candidate_id": target.get("candidate_id"),
            "company_id": target.get("company_id"),
            "trend": trend,
            "trend_data_points": len(data),
            "trend_score_points": len(score_points),
            "latest_score": latest_score,
            "latest_level": latest_level,
            "previous_score": previous_score,
            "delta": delta,
            "latest_checked_at": latest.get("date") if latest else None,
            "comparison_text": comparison_text,
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
        score_value = item.get("latest_score")
        score_text = f"当前风险评分：{score_value:g}/100" if isinstance(score_value, (int, float)) else "当前风险评分：暂无"
        level_text = f"风险等级：{item['latest_level']}" if item.get("latest_level") else "风险等级：暂无"
        payload["claims"].append({
            "claim_id": f"{evidence_id}:claim:{item['monitor_target_id'] or item['company_name']}",
            "entity_id": "watchlist",
            "dimension": "risk_monitoring",
            "statement": (
                f"{item['company_name']} 最近 {period_months} 个月风险变化：{item['trend']}；"
                f"{score_text}；{level_text}；{item['comparison_text']}"
                f"（{item['trend_data_points']} 个快照，{item['trend_score_points']} 个评分）"
            ),
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

    tasks = list_review_tasks(None, user_id, user_role or "purchaser")
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
    from app.domains.alert.service import _find_watchlist_target
    existing_target = _find_watchlist_target(
        monitor_target_id=monitor_target_id,
        supplier_id=supplier_id,
        candidate_id=candidate_id,
        company_id=company_id,
        company_name=company_name.strip() or None,
    )
    if existing_target and existing_target.get("monitor_status", "active") == "active":
        return {
            **existing_target,
            "status": "already_watching",
            "message": f"{existing_target.get('company_name') or company_name} 已在当前监控清单中，无需重复加入。",
            "side_effect_receipt": {
                "operation": "add_to_watchlist",
                "status": "already_watching",
                "monitor_target_id": existing_target.get("monitor_target_id"),
            },
        }
    # A free-text external company must first be resolved to a formal
    # supplier or verified company identity.  Never create a monitoring row
    # merely because the user typed a recognizable company name.
    if target_type == "external_candidate" and not company_id:
        return {
            "status": "needs_review",
            "error": "identity_required",
            "message": "该外部候选尚未完成主体核验，暂不能加入监控清单。请先完成主体检索与确认。",
        }
    if not existing_target and not monitor_target_id and not supplier_id and not company_id and target_type is not None:
        from app.domains.sourcing.supplier_repo import resolve_supplier_id

        if not resolve_supplier_id(company_name.strip()):
            return {
                "status": "needs_review",
                "error": "identity_required",
                "message": "该企业尚未完成主体核验，暂不能加入监控清单。请先完成主体检索与确认。",
            }
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
    # Establish a first risk baseline as part of the approved add operation.
    # This uses cached evidence only; unavailable evidence must not roll back
    # the monitoring write, but the result must say so explicitly.
    baseline: dict[str, object] = {
        "risk_baseline_status": "not_available",
        "risk_baseline_message": "当前没有可用于计算风险基线的企业资料。",
    }
    try:
        from app.domains.alert.service import save_snapshot
        from app.domains.risk.service import calculate_company_risk_preview

        preview = calculate_company_risk_preview(result.get("company_name") or company_name)
        if preview is not None:
            preview_data = preview.model_dump(mode="json")
            save_snapshot(
                result.get("company_name") or company_name,
                preview,
                monitor_target_id=result.get("monitor_target_id") or monitor_target_id,
                target_type=result.get("target_type") or target_type,
                supplier_id=result.get("supplier_id") or supplier_id,
                candidate_id=result.get("candidate_id") or candidate_id,
                company_id=result.get("company_id") or company_id,
            )
            baseline = {
                "risk_baseline_status": "created",
                "risk_baseline_message": "已基于当前可用资料建立风险基线。",
                "risk_score": preview_data.get("risk_score"),
                "risk_level": preview_data.get("risk_level"),
                "score_breakdown": preview_data.get("score_breakdown"),
                "risk_detail": preview_data.get("risk_detail"),
                "financial": preview_data.get("financial"),
            }
    except Exception as exc:
        from app.core.logging import get_logger

        get_logger(__name__).warning(
            "watchlist_risk_baseline_failed",
            company=result.get("company_name") or company_name,
            error=str(exc),
        )
    return {
        **result,
        **baseline,
        "side_effect_receipt": {
            "receipt_id": active_context.idempotency_key if active_context else f"watchlist:add:{company_name}",
            "operation": "add_to_watchlist",
            "company_name": result.get("company_name") or company_name,
            "monitor_target_id": result.get("monitor_target_id") or monitor_target_id,
            "supplier_id": result.get("supplier_id") or supplier_id,
            "candidate_id": result.get("candidate_id") or candidate_id,
            "company_id": result.get("company_id") or company_id,
            "risk_baseline_status": baseline.get("risk_baseline_status"),
            "risk_score": baseline.get("risk_score"),
            "risk_level": baseline.get("risk_level"),
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
