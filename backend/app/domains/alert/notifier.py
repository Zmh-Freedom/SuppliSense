"""主动预警通知服务 — 定时检查风险变化并通过 WebSocket 推送。"""

import asyncio
from datetime import datetime, timedelta, timezone

from app.db.mongo import get_db
from app.core.logging import get_logger

logger = get_logger()

SCORE_THRESHOLD = 10  # 风险评分变化阈值


def check_and_notify() -> dict:
    """检查监控列表企业风险变化，触发 WebSocket 推送。
    当风险评分变化 >= SCORE_THRESHOLD 或等级升级时，同时触发寻源替代建议（24h 限频）。
    """
    from app.domains.alert.service import detect_changes, get_watchlist_targets
    from app.services.ws_manager import ws_manager

    targets = get_watchlist_targets()
    if not targets:
        return {"checked": 0, "alerts": []}

    db = get_db()
    alerts = []
    suggestions_sent = 0

    for target in targets:
        company_name = target.get("company_name", "")
        monitor_target_id = target.get("monitor_target_id")
        try:
            changes = detect_changes(company_name, monitor_target_id=monitor_target_id)
            if not changes.get("changed"):
                continue

            score_delta = 0
            level_escalated = False

            for change in changes.get("changes", []):
                field = change.get("field", "")
                if field == "风险评分":
                    try:
                        old = float(change.get("old", 0))
                        new = float(change.get("new", 0))
                        score_delta = max(score_delta, new - old)
                    except (TypeError, ValueError):
                        pass
                elif field == "风险等级":
                    new_level = change.get("new", "")
                    if new_level in ("high", "critical"):
                        level_escalated = True

            if score_delta < SCORE_THRESHOLD and not level_escalated:
                continue

            # ---- risk_alert notification ----
            alert_doc = {
                "company": company_name,
                "company_name": company_name,
                "monitor_target_id": monitor_target_id,
                "target_type": target.get("target_type"),
                "supplier_id": target.get("supplier_id"),
                "candidate_id": target.get("candidate_id"),
                "company_id": target.get("company_id"),
                "recipient_user_id": target.get("owner_user_id"),
                "score_delta": score_delta,
                "changes": changes.get("changes", []),
                "timestamp": datetime.now(timezone.utc),
                "notified": True,
            }
            if alert_doc["recipient_user_id"]:
                db["notifications"].insert_one(alert_doc)

            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(ws_manager.broadcast("risk_alert", {
                    "company": company_name,
                    "score_delta": score_delta,
                    "changes": changes.get("changes", []),
                }))
            else:
                asyncio.run(ws_manager.broadcast("risk_alert", {
                    "company": company_name,
                    "score_delta": score_delta,
                    "changes": changes.get("changes", []),
                }))

            alerts.append({"company": company_name, "score_delta": score_delta})
            logger.warning("risk_alert_notified", company=company_name, delta=score_delta)

            # ---- sourcing suggestion (24h throttled) ----
            should_suggest = score_delta >= SCORE_THRESHOLD or level_escalated
            if should_suggest:
                now = datetime.now(timezone.utc)
                cutoff = now - timedelta(hours=24)
                existing = db["notifications"].find_one({
                    "company": company_name,
                    "type": "sourcing_suggestion",
                    "recipient_user_id": target.get("owner_user_id"),
                    "timestamp": {"$gte": cutoff},
                })
                if not existing:
                    try:
                        from app.domains.sourcing.service import get_top_alternatives
                        alternatives = get_top_alternatives(company_name, top_k=3)
                        if alternatives:
                            suggestion_doc = {
                                "company": company_name,
                                "type": "sourcing_suggestion",
                                "alternatives": alternatives,
                                "timestamp": now,
                                "read": False,
                                "recipient_user_id": target.get("owner_user_id"),
                            }
                            if suggestion_doc["recipient_user_id"]:
                                db["notifications"].insert_one(suggestion_doc)

                            if loop.is_running():
                                loop.create_task(ws_manager.broadcast("sourcing_suggestion", {
                                    "company": company_name,
                                    "risk_score": score_delta,
                                    "level_escalated": level_escalated,
                                    "alternatives": alternatives,
                                }))
                            else:
                                asyncio.run(ws_manager.broadcast("sourcing_suggestion", {
                                    "company": company_name,
                                    "risk_score": score_delta,
                                    "level_escalated": level_escalated,
                                    "alternatives": alternatives,
                                }))

                            suggestions_sent += 1
                            logger.info("sourcing_suggestion_sent", company=company_name, alternatives=len(alternatives))
                    except Exception as e:
                        logger.error("sourcing_suggestion_failed", company=company_name, error=str(e))

        except Exception as e:
            logger.error("alert_check_failed", company=company_name, error=str(e))

    return {"checked": len(targets), "alerts": alerts, "suggestions_sent": suggestions_sent}


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
