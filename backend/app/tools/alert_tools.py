"""预警监控工具。"""
from langchain_core.tools import tool


@tool
def check_alert(company_name: str) -> dict:
    """查看企业预警变化。

    Args:
        company_name: 企业全称
    """
    from app.services.alert_service import detect_changes, get_latest_snapshot
    if not get_latest_snapshot(company_name):
        return {"message": "暂无历史快照"}
    return detect_changes(company_name)


@tool
def get_watchlist() -> dict:
    """获取监控清单。"""
    from app.services.alert_service import get_watchlist as _get_watchlist
    companies = _get_watchlist()
    return {"count": len(companies), "companies": companies}


@tool
def add_to_watchlist(company_name: str) -> dict:
    """将企业加入监控清单。

    Args:
        company_name: 企业全称
    """
    from app.services.alert_service import add_to_watchlist as _add
    return _add(company_name)


@tool
def remove_from_watchlist(company_name: str) -> dict:
    """将企业从监控清单移除。

    Args:
        company_name: 企业全称
    """
    from app.services.alert_service import remove_from_watchlist as _remove
    return _remove(company_name)
