"""预警监控工具。"""
from langchain_core.tools import tool


@tool
def check_alert(company_name: str) -> dict:
    """查看企业预警变化。

    Args:
        company_name: 企业全称
    """
    from app.domains.alert.service import detect_changes, get_latest_snapshot
    if not get_latest_snapshot(company_name):
        return {"message": "暂无历史快照"}
    return detect_changes(company_name)


@tool
def get_watchlist() -> dict:
    """获取监控清单。"""
    from app.domains.alert.service import get_watchlist as _get_watchlist
    companies = _get_watchlist()
    return {"count": len(companies), "companies": companies}


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
    from app.domains.alert.service import get_watchlist as _get_watchlist

    companies = _get_watchlist()
    if not companies:
        return {"count": 0, "message": "监控清单为空", "companies": []}

    db = get_db()
    days = period_months * 30
    since = datetime.now(timezone.utc) - timedelta(days=days)
    results = []

    for name in companies:
        snapshots = list(
            db["alert_snapshots"]
            .find(
                {"company_name": name, "checked_at": {"$gte": since}},
                {"checked_at": 1, "risk_score": 1, "risk_level": 1, "_id": 0},
            )
            .sort("checked_at", 1)
        )
        data = [
            {"date": s["checked_at"].strftime("%Y-%m-%d"), "risk_score": s.get("risk_score", 0), "risk_level": s.get("risk_level", "")}
            for s in snapshots
        ]
        trend = "稳定"
        if len(data) >= 2:
            delta = data[-1]["risk_score"] - data[0]["risk_score"]
            trend = "恶化" if delta > 10 else "改善" if delta < -10 else "稳定"

        results.append({
            "company_name": name,
            "trend": trend,
            "latest_score": data[-1]["risk_score"] if data else None,
            "latest_level": data[-1]["risk_level"] if data else None,
            "data": data,
        })

    return {"count": len(results), "period_months": period_months, "companies": results}


@tool
def add_to_watchlist(company_name: str) -> dict:
    """将企业加入监控清单（需要用户确认）。

    Args:
        company_name: 企业全称
    """
    from app.graphs.approval import needs_approval, request_approval

    if needs_approval("add_to_watchlist", {"company_name": company_name}):
        try:
            approved = request_approval("add_to_watchlist", {"company_name": company_name})
        except RuntimeError:
            return {"success": False, "error": "approval_context_required", "message": "加入监控必须在支持人工审批的 Agent 会话中执行"}
        if not approved:
            return {"cancelled": True, "message": f"用户取消了将 {company_name} 加入监控清单的操作"}

    from app.domains.alert.service import add_to_watchlist as _add
    return _add(company_name)


@tool
def remove_from_watchlist(company_name: str) -> dict:
    """将企业从监控清单移除（需要用户确认）。

    Args:
        company_name: 企业全称
    """
    from app.graphs.approval import needs_approval, request_approval

    if needs_approval("remove_from_watchlist", {"company_name": company_name}):
        try:
            approved = request_approval("remove_from_watchlist", {"company_name": company_name})
        except RuntimeError:
            return {"success": False, "error": "approval_context_required", "message": "移出监控必须在支持人工审批的 Agent 会话中执行"}
        if not approved:
            return {"cancelled": True, "message": f"用户取消了将 {company_name} 移出监控清单的操作"}

    from app.domains.alert.service import remove_from_watchlist as _remove
    return _remove(company_name)
