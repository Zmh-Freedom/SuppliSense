"""主动预警通知服务 — 定时检查风险变化并通过 WebSocket 推送。"""

from datetime import datetime, timezone

from app.db.mongo import get_db
from app.core.logging import get_logger

logger = get_logger()

SCORE_THRESHOLD = 10  # 风险评分变化阈值


async def check_and_notify() -> dict:
    """检查监控列表企业风险变化，触发 WebSocket 推送。"""
    from app.services.alert_service import detect_changes, get_watchlist
    from app.services.ws_manager import ws_manager

    companies = get_watchlist()
    if not companies:
        return {"checked": 0, "alerts": []}

    db = get_db()
    alerts = []

    for company_name in companies:
        try:
            changes = detect_changes(company_name)
            if not changes.get("changed"):
                continue

            # 检查评分变化是否超过阈值
            score_delta = 0
            for change in changes.get("changes", []):
                if "score" in str(change).lower():
                    # 尝试提取分数变化
                    try:
                        old = change.get("old", 0)
                        new = change.get("new", 0)
                        if isinstance(old, (int, float)) and isinstance(new, (int, float)):
                            score_delta = max(score_delta, abs(new - old))
                    except (TypeError, AttributeError):
                        pass

            if score_delta >= SCORE_THRESHOLD:
                alert_doc = {
                    "company": company_name,
                    "score_delta": score_delta,
                    "changes": changes.get("changes", []),
                    "timestamp": datetime.now(timezone.utc),
                    "notified": True,
                }
                db["notifications"].insert_one(alert_doc)

                # WebSocket 广播
                await ws_manager.broadcast("risk_alert", {
                    "company": company_name,
                    "score_delta": score_delta,
                    "changes": changes.get("changes", []),
                })

                alerts.append({"company": company_name, "score_delta": score_delta})
                logger.warning("risk_alert_notified", company=company_name, delta=score_delta)

        except Exception as e:
            logger.error("alert_check_failed", company=company_name, error=str(e))

    return {"checked": len(companies), "alerts": alerts}


def get_notifications(limit: int = 20) -> list[dict]:
    """获取最近的预警通知记录。"""
    db = get_db()
    docs = list(
        db["notifications"]
        .find({}, {"_id": 0})
        .sort("timestamp", -1)
        .limit(limit)
    )
    return docs
