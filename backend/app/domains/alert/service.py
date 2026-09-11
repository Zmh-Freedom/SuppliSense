import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from app.db.mongo import get_db
from app.schemas import RiskCalculateResponse

logger = logging.getLogger(__name__)

MONITOR_TARGET_TYPES = {"formal_supplier", "external_candidate", "company"}


def _target_query(
    *,
    monitor_target_id: str | None = None,
    supplier_id: str | None = None,
    candidate_id: str | None = None,
    company_id: str | None = None,
    company_name: str | None = None,
) -> dict:
    """Build a stable monitor-target query, falling back to legacy names."""
    if monitor_target_id:
        return {"monitor_target_id": monitor_target_id}
    if supplier_id:
        return {"supplier_id": supplier_id}
    if candidate_id:
        return {"candidate_id": candidate_id}
    if company_id:
        return {"company_id": company_id}
    if company_name:
        return {"company_name": company_name}
    return {}


def _target_from_document(document: dict) -> dict:
    """Return a stable, JSON-safe monitor target view."""
    target = dict(document)
    target.pop("_id", None)
    name = str(target.get("display_name") or target.get("company_name") or "").strip()
    target["company_name"] = name
    target["display_name"] = name
    target["monitor_status"] = target.get("monitor_status") or "active"
    target["target_type"] = target.get("target_type") or (
        "external_candidate" if target.get("candidate_id") else
        "formal_supplier" if target.get("supplier_id") else "company"
    )
    target["identity_status"] = target.get("identity_status") or (
        "candidate" if target["target_type"] == "external_candidate" else
        "verified" if target.get("supplier_id") or target.get("company_id") else "unresolved"
    )
    return target


def _can_access_target(target: dict, user_id: str | None, user_role: str | None) -> bool:
    """Enforce purchaser visibility for a monitoring object.

    Legacy rows without a supplier identity are administrator-only until an
    owner is explicitly backfilled.  This is deliberately fail-closed.
    """
    # Internal schedulers call without an actor and must retain their global
    # operational view. Every user-facing API passes both values explicitly.
    if user_id is None and user_role is None:
        return True
    if user_role == "admin":
        return True
    if not user_id:
        return False
    supplier_id = str(target.get("supplier_id") or "")
    if supplier_id:
        from app.domains.supplier.access import can_access_supplier

        return can_access_supplier(supplier_id, str(user_id), str(user_role or ""))
    # External candidates and unresolved companies have no formal purchaser
    # assignment. They remain private to the user who investigated them.
    return str(target.get("owner_user_id") or "") == str(user_id)


def get_watchlist_targets(user_id: str | None = None, user_role: str | None = None) -> list[dict]:
    """Return complete monitoring objects and lazily backfill legacy rows."""
    db = get_db()
    targets: list[dict] = []
    for document in db["watchlist"].find().sort("added_at", 1):
        if not isinstance(document, dict):
            continue
        target = _target_from_document(document)
        if not target.get("monitor_target_id"):
            target["monitor_target_id"] = str(uuid.uuid4())
            db["watchlist"].update_one(
                {"_id": document.get("_id")} if document.get("_id") is not None else {
                    "company_name": target["company_name"]
                },
                {"$set": {
                    "monitor_target_id": target["monitor_target_id"],
                    "target_type": target["target_type"],
                    "identity_status": target["identity_status"],
                    "monitor_status": target["monitor_status"],
                    "display_name": target["display_name"],
                }},
            )
        if _can_access_target(target, user_id, user_role):
            targets.append(target)
    return targets


def _find_watchlist_target(
    *,
    monitor_target_id: str | None = None,
    supplier_id: str | None = None,
    candidate_id: str | None = None,
    company_id: str | None = None,
    company_name: str | None = None,
) -> dict | None:
    db = get_db()
    query = _target_query(
        monitor_target_id=monitor_target_id,
        supplier_id=supplier_id,
        candidate_id=candidate_id,
        company_id=company_id,
        company_name=company_name,
    )
    if not query:
        return None
    document = db["watchlist"].find_one(query)
    if not isinstance(document, dict):
        return None
    target = _target_from_document(document)
    if target.get("monitor_target_id"):
        return target
    target["monitor_target_id"] = str(uuid.uuid4())
    db["watchlist"].update_one(
        {"_id": document.get("_id")} if document.get("_id") is not None else query,
        {"$set": {
            "monitor_target_id": target["monitor_target_id"],
            "target_type": target["target_type"],
            "identity_status": target["identity_status"],
            "monitor_status": target["monitor_status"],
            "display_name": target["display_name"],
        }},
    )
    return target


def _target_snapshots(db: object, target: dict, limit: int = 2) -> list[dict]:
    """Load the latest snapshots for one monitor target without mixing identities."""
    target_id = target.get("monitor_target_id")
    company_name = target.get("company_name")
    query = {"monitor_target_id": target_id} if target_id else {"company_name": company_name}
    snapshots = list(
        db["alert_snapshots"]
        .find(query)
        .sort([("checked_at", -1), ("snapshot_version", -1)])
        .limit(limit)
    )
    if not snapshots and target_id and company_name:
        # Compatibility only for snapshots written before monitor_target_id existed.
        snapshots = list(
            db["alert_snapshots"]
            .find({"company_name": company_name})
            .sort([("checked_at", -1), ("snapshot_version", -1)])
            .limit(limit)
        )
    return [snapshot for snapshot in snapshots if isinstance(snapshot, dict)]


def _has_transaction_data(db: object, target: dict) -> bool:
    """Check whether a formal supplier has current internal transaction evidence."""
    supplier_code = target.get("supplier_code")
    supplier_id = target.get("supplier_id")
    if not supplier_code and not supplier_id:
        return False
    query: dict = {"sync_status": "current"}
    query["supplier_code" if supplier_code else "supplier_id"] = supplier_code or supplier_id
    try:
        return db["supplier_transaction_snapshots"].find_one(query) is not None
    except Exception:
        return False


def _data_coverage(db: object, target: dict, snapshot: dict | None) -> dict:
    """Build user-facing coverage semantics without treating missing data as low risk."""
    risk_detail = (snapshot or {}).get("risk_detail") or {}
    snapshot_coverage = risk_detail.get("data_coverage") if isinstance(risk_detail, dict) else {}
    available_dimensions = set((snapshot_coverage or {}).get("available_dimensions") or [])
    identity_status = target.get("identity_status") or "unresolved"
    transaction_available = _has_transaction_data(db, target)
    try:
        sentiment_available = db["sentiment_results"].find_one({"company_name": target.get("company_name")}) is not None
    except Exception:
        sentiment_available = False
    dimensions = [
        {
            "key": "identity",
            "label": "主体身份",
            "status": "available" if identity_status == "verified" else "pending",
            "detail": "已核验" if identity_status == "verified" else "待核验",
        },
        {
            "key": "risk_snapshot",
            "label": "风险快照",
            "status": "available" if snapshot else "missing",
            "detail": "已有评估快照" if snapshot else "尚未完成首次评估",
        },
        {
            "key": "financial",
            "label": "财务/公开信息",
            "status": "available" if (snapshot or "financial" in available_dimensions) and ((snapshot or {}).get("financial") or "financial" in available_dimensions) else "missing",
            "detail": "已获取" if (snapshot or {}).get("financial") or "financial" in available_dimensions else "当前未覆盖",
        },
        {
            "key": "transaction",
            "label": "内部交易",
            "status": "available" if transaction_available else "missing",
            "detail": "已有供应商月度数据" if transaction_available else "暂无内部交易数据",
        },
        {
            "key": "sentiment",
            "label": "舆情",
            "status": "available" if sentiment_available else "missing",
            "detail": "已有舆情分析" if sentiment_available else "尚未形成舆情分析",
        },
    ]
    available_count = sum(item["status"] == "available" for item in dimensions)
    ratio = round(available_count / len(dimensions), 2) if dimensions else 0.0
    status = "complete" if available_count == len(dimensions) else "partial" if available_count else "insufficient"
    return {
        "status": status,
        "available_count": available_count,
        "total_count": len(dimensions),
        "coverage_ratio": ratio,
        "summary": f"{available_count}/{len(dimensions)} 个数据域可用",
        "dimensions": dimensions,
        "missing_dimensions": [item["label"] for item in dimensions if item["status"] != "available"],
    }


def _risk_change(snapshots: list[dict]) -> dict:
    """Return an explicit trend state; no history is never labelled stable."""
    if not snapshots:
        return {"status": "no_data", "label": "暂无快照", "delta": None, "previous_score": None}
    current_score = snapshots[0].get("risk_score")
    if len(snapshots) < 2 or current_score is None or snapshots[1].get("risk_score") is None:
        return {"status": "insufficient_data", "label": "数据不足", "delta": None, "previous_score": None}
    previous_score = snapshots[1].get("risk_score")
    delta = round(float(current_score) - float(previous_score), 1)
    if delta >= 10:
        status, label = "deteriorating", "风险恶化"
    elif delta <= -10:
        status, label = "improving", "风险改善"
    else:
        status, label = "stable", "变化不明显"
    return {"status": status, "label": label, "delta": delta, "previous_score": previous_score}


def _next_action(target: dict, snapshot: dict | None, risk_change: dict, coverage: dict) -> dict:
    """Translate evidence state into a procurement review action."""
    if target.get("identity_status") != "verified":
        priority = "high" if target.get("target_type") == "external_candidate" else "medium"
        reason = "外部候选尚未完成主体确认" if target.get("target_type") == "external_candidate" else "监控对象尚未完成主体确认"
        return {"code": "verify_identity", "label": "完成主体核验", "priority": priority, "reason": reason}
    if not snapshot:
        return {"code": "assess", "label": "执行首次评估", "priority": "high", "reason": "暂无风险快照，不能判断风险变化"}
    if risk_change["status"] == "deteriorating" or snapshot.get("risk_level") == "高风险":
        return {"code": "review", "label": "优先采购复核", "priority": "high", "reason": "风险分数上升或当前处于高风险"}
    if coverage["status"] != "complete":
        return {"code": "supplement_data", "label": "核验数据覆盖范围", "priority": "medium", "reason": "部分数据域当前未覆盖，不能形成完整结论"}
    return {"code": "continue_monitoring", "label": "继续观察", "priority": "low", "reason": "当前已有完整覆盖且未发现明显恶化"}


def get_watchlist_target_summaries(
    user_id: str | None = None,
    user_role: str | None = None,
) -> list[dict]:
    """Return monitor targets enriched for the procurement review workbench."""
    db = get_db()
    summaries = []
    targets = (
        get_watchlist_targets()
        if user_id is None and user_role is None
        else get_watchlist_targets(user_id, user_role)
    )
    for target in targets:
        snapshots = _target_snapshots(db, target)
        latest = snapshots[0] if snapshots else None
        risk_change = _risk_change(snapshots)
        coverage = _data_coverage(db, target, latest)
        next_action = _next_action(target, latest, risk_change, coverage)
        enriched = dict(target)
        try:
            from app.domains.alert.review_tasks import get_latest_review_task
            review_task = get_latest_review_task(target.get("monitor_target_id"))
        except Exception:
            # The monitoring dashboard remains readable while PostgreSQL is
            # unavailable; task details will appear once the store recovers.
            review_task = None
        enriched.update({
            "risk_score": latest.get("risk_score") if latest else None,
            "risk_level": latest.get("risk_level") if latest else None,
            "risk_change": risk_change,
            "data_coverage": coverage,
            "last_checked_at": latest.get("checked_at") if latest else None,
            "next_action": next_action,
            "review_task": review_task,
        })
        summaries.append(enriched)
    return summaries


def get_watchlist_target_risk_detail(
    monitor_target_id: str,
    user_id: str | None = None,
    user_role: str | None = None,
) -> dict | None:
    """Return the latest auditable risk snapshot for one stable monitor target."""
    target = _find_watchlist_target(monitor_target_id=monitor_target_id)
    if target is None:
        return None
    if not _can_access_target(target, user_id, user_role):
        return None

    db = get_db()
    snapshots = _target_snapshots(db, target, limit=20)
    latest = snapshots[0] if snapshots else None
    history = [
        {
            "snapshot_id": snapshot.get("snapshot_id"),
            "snapshot_version": snapshot.get("snapshot_version"),
            "checked_at": snapshot.get("checked_at"),
            "risk_score": snapshot.get("risk_score"),
            "risk_level": snapshot.get("risk_level"),
        }
        for snapshot in snapshots
    ]
    if latest is None:
        return {
            "monitor_target_id": monitor_target_id,
            "company_name": target.get("company_name"),
            "has_snapshot": False,
            "risk_change": _risk_change(snapshots),
            "latest_snapshot": None,
            "history": history,
        }
    return {
        "monitor_target_id": monitor_target_id,
        "company_name": target.get("company_name"),
        "has_snapshot": True,
        "risk_change": _risk_change(snapshots),
        "latest_snapshot": {
            "snapshot_id": latest.get("snapshot_id"),
            "snapshot_version": latest.get("snapshot_version"),
            "checked_at": latest.get("checked_at"),
            "risk_score": latest.get("risk_score"),
            "risk_level": latest.get("risk_level"),
            "scoring_version": latest.get("scoring_version"),
            "score_breakdown": latest.get("score_breakdown") or {},
            "risk_detail": latest.get("risk_detail") or {},
            "financial": latest.get("financial") or {},
        },
        "history": history,
    }


def _broadcast_alert_update() -> None:
    """Notify all WebSocket clients that alert data changed."""
    try:
        from app.services.ws_manager import ws_manager
        db = get_db()
        count = db["alerts"].count_documents({})
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(ws_manager.broadcast("alert_update", {"count": count}))
        else:
            asyncio.run(ws_manager.broadcast("alert_update", {"count": count}))
    except Exception:
        pass  # WebSocket push is best-effort


def save_snapshot(
    company_name: str,
    result: RiskCalculateResponse,
    *,
    monitor_target_id: str | None = None,
    target_type: str | None = None,
    supplier_id: str | None = None,
    candidate_id: str | None = None,
    company_id: str | None = None,
) -> None:
    from app.domains.risk.service import SCORING_VERSION
    from app.domains.sourcing.supplier_repo import resolve_supplier_id

    db = get_db()
    resolved_supplier_id = supplier_id
    if resolved_supplier_id is None and not candidate_id and not company_id:
        resolved_supplier_id = resolve_supplier_id(company_name)
    target = _find_watchlist_target(
        monitor_target_id=monitor_target_id,
        supplier_id=resolved_supplier_id,
        candidate_id=candidate_id,
        company_id=company_id,
        company_name=company_name,
    )
    stable_target_id = monitor_target_id or (target or {}).get("monitor_target_id")
    resolved_target_type = target_type or (target or {}).get("target_type")
    if resolved_target_type not in MONITOR_TARGET_TYPES:
        resolved_target_type = (
            "external_candidate" if candidate_id else
            "formal_supplier" if resolved_supplier_id else "company"
        )
    resolved_candidate_id = candidate_id or (target or {}).get("candidate_id")
    resolved_company_id = company_id or (target or {}).get("company_id")
    resolved_supplier_code = (target or {}).get("supplier_code")
    latest_query = (
        {"monitor_target_id": stable_target_id}
        if stable_target_id
        else {"company_name": company_name}
    )
    latest = db["alert_snapshots"].find_one(
        latest_query,
        sort=[("snapshot_version", -1), ("checked_at", -1)],
    )
    previous_snapshot_id = latest.get("snapshot_id") if isinstance(latest, dict) else None
    previous_version = latest.get("snapshot_version", 0) if isinstance(latest, dict) else 0
    snapshot_version = previous_version + 1 if isinstance(previous_version, int) else 1
    snapshot_id = str(uuid.uuid4())
    doc = {
        "snapshot_id": snapshot_id,
        "snapshot_version": snapshot_version,
        "previous_snapshot_id": previous_snapshot_id,
        "company_name": company_name,
        "monitor_target_id": stable_target_id,
        "target_type": resolved_target_type,
        "supplier_id": resolved_supplier_id,
        "candidate_id": resolved_candidate_id,
        "company_id": resolved_company_id,
        "supplier_code": resolved_supplier_code,
        "checked_at": datetime.now(timezone.utc),
        "risk_score": result.risk_score,
        "risk_level": result.risk_level,
        "risk_detail": result.risk_detail,
        "financial": result.financial.model_dump() if result.financial else None,
        "score_breakdown": result.score_breakdown,
        "scoring_version": SCORING_VERSION,
    }
    db["alert_snapshots"].insert_one(doc)


def get_snapshot_history(
    company_name: str,
    limit: int = 50,
    *,
    monitor_target_id: str | None = None,
) -> list[dict]:
    """Return append-only risk assessment snapshots for audit and monitoring review."""
    bounded_limit = max(1, min(limit, 100))
    db = get_db()
    query = _target_query(monitor_target_id=monitor_target_id, company_name=company_name)
    snapshots = list(
        db["alert_snapshots"]
        .find(query)
        .sort([("snapshot_version", -1), ("checked_at", -1)])
        .limit(bounded_limit)
    )
    if not snapshots and monitor_target_id:
        return list(
            db["alert_snapshots"]
            .find({"company_name": company_name})
            .sort([("snapshot_version", -1), ("checked_at", -1)])
            .limit(bounded_limit)
        )
    return snapshots


def get_latest_snapshot(
    company_name: str,
    *,
    monitor_target_id: str | None = None,
) -> dict | None:
    db = get_db()
    snapshot = db["alert_snapshots"].find_one(
        _target_query(monitor_target_id=monitor_target_id, company_name=company_name),
        sort=[("checked_at", -1)],
    )
    if snapshot is None and monitor_target_id and company_name:
        # Legacy snapshots predate monitor_target_id; keep them readable during migration.
        return db["alert_snapshots"].find_one(
            {"company_name": company_name}, sort=[("checked_at", -1)]
        )
    return snapshot


def detect_changes(
    company_name: str,
    *,
    monitor_target_id: str | None = None,
) -> dict:
    """Compare latest snapshot with second-latest, return changes."""
    db = get_db()
    snapshots = list(
        db["alert_snapshots"]
        .find(_target_query(monitor_target_id=monitor_target_id, company_name=company_name))
        .sort("checked_at", -1)
        .limit(2)
    )
    if len(snapshots) < 2 and monitor_target_id:
        snapshots = list(
            db["alert_snapshots"]
            .find({"company_name": company_name})
            .sort("checked_at", -1)
            .limit(2)
        )
    if len(snapshots) < 2:
        return {
            "company_name": company_name,
            "monitor_target_id": monitor_target_id,
            "changed": False,
            "changes": [],
        }

    new = snapshots[0]
    old = snapshots[1]
    changes = []

    def compare(field, old_val, new_val):
        if old_val != new_val:
            changes.append({"field": field, "old": old_val, "new": new_val})

    nd = new.get("risk_detail") or {}
    od = old.get("risk_detail") or {}
    compare("诉讼数量", od.get("lawsuit_count"), nd.get("lawsuit_count"))
    compare("被执行记录", od.get("executed_count"), nd.get("executed_count"))
    compare("失信记录", od.get("dishonesty_count"), nd.get("dishonesty_count"))
    compare("经营异常", od.get("abnormal_operation_count"), nd.get("abnormal_operation_count"))
    compare("行政处罚", od.get("administrative_penalty_count"), nd.get("administrative_penalty_count"))

    if not od.get("major_lawsuit") and nd.get("major_lawsuit"):
        changes.append({"field": "重大诉讼", "old": False, "new": True})

    compare("风险评分", old.get("risk_score"), new.get("risk_score"))
    compare("风险等级", old.get("risk_level"), new.get("risk_level"))

    nf = new.get("financial") or {}
    of = old.get("financial") or {}
    if of and nf:
        if (of.get("net_profit_growth") or 0) > 0 and (nf.get("net_profit_growth") or 0) < 0:
            changes.append({"field": "净利润", "old": "正增长", "new": "转为负增长"})
        compare("资产负债率", of.get("debt_ratio"), nf.get("debt_ratio"))

    severity = "critical" if any(
        "被执行" in c["field"] or "失信" in c["field"] or "重大诉讼" in c["field"]
        for c in changes
    ) else "warning" if changes else "normal"

    if changes:
        # apply custom alert rules
        from app.domains.alert.rules import evaluate_changes, get_rules
        rules = get_rules(company_name)
        triggered = evaluate_changes(rules, changes)

        if triggered:
            final_severity = "critical" if any(t["severity"] == "critical" for t in triggered) else "warning"
            from app.domains.sourcing.supplier_repo import resolve_supplier_id
            db["alerts"].insert_one({
                "company_name": company_name,
                "monitor_target_id": monitor_target_id or new.get("monitor_target_id"),
                "target_type": new.get("target_type"),
                "supplier_id": new.get("supplier_id") or resolve_supplier_id(company_name),
                "candidate_id": new.get("candidate_id"),
                "company_id": new.get("company_id"),
                "supplier_code": new.get("supplier_code"),
                "created_at": datetime.now(timezone.utc),
                "changes": triggered,
                "severity": final_severity,
                "read": False,
            })
            try:
                target_doc = db["watchlist"].find_one(
                    {"monitor_target_id": monitor_target_id or new.get("monitor_target_id")}
                )
            except Exception:
                target_doc = None
            target_doc = target_doc or {
                "company_name": company_name,
                "monitor_target_id": monitor_target_id or new.get("monitor_target_id"),
                "supplier_id": new.get("supplier_id"),
                "candidate_id": new.get("candidate_id"),
                "company_id": new.get("company_id"),
                "target_type": new.get("target_type"),
            }
            try:
                from app.domains.alert.notifier import notify_target_change

                notify_target_change(target_doc, triggered, final_severity)
            except Exception as exc:
                logger.warning("scoped_alert_delivery_failed", company=company_name, error=str(exc))

    return {
        "company_name": company_name,
        "monitor_target_id": monitor_target_id or new.get("monitor_target_id"),
        "target_type": new.get("target_type"),
        "supplier_id": new.get("supplier_id"),
        "candidate_id": new.get("candidate_id"),
        "company_id": new.get("company_id"),
        "changed": len(changes) > 0,
        "severity": severity,
        "changes": changes,
        "previous": old.get("checked_at").isoformat() if old.get("checked_at") else None,
        "current": new.get("checked_at").isoformat() if new.get("checked_at") else None,
    }


def add_to_watchlist(
    company_name: str | None = None,
    *,
    target_type: str | None = None,
    monitor_target_id: str | None = None,
    supplier_id: str | None = None,
    candidate_id: str | None = None,
    company_id: str | None = None,
    supplier_code: str | None = None,
    owner_user_id: str | None = None,
) -> dict:
    from app.domains.sourcing.supplier_repo import resolve_supplier_id

    db = get_db()
    name = (company_name or "").strip()
    if target_type and target_type not in MONITOR_TARGET_TYPES:
        raise ValueError(f"不支持的监控对象类型: {target_type}")

    # Look up the existing monitor object before resolving a name to a
    # supplier identity. Legacy rows are uniquely indexed by company_name;
    # resolving the same name to a supplier first would change the upsert
    # query and attempt to insert a duplicate legacy row.
    existing = _find_watchlist_target(
        monitor_target_id=monitor_target_id,
        supplier_id=supplier_id,
        candidate_id=candidate_id,
        company_id=company_id,
        company_name=name or None,
    )

    # Resolve existing identities only. Monitoring must never create a supplier.
    sid = supplier_id
    supplier_doc = None
    if existing:
        name = name or existing.get("company_name", "")
        sid = sid or existing.get("supplier_id")
        candidate_id = candidate_id or existing.get("candidate_id")
        company_id = company_id or existing.get("company_id")
        supplier_code = supplier_code or existing.get("supplier_code")
    elif sid:
        supplier_doc = db["suppliers"].find_one({"_id": sid})
        name = name or (supplier_doc or {}).get("name", "")
    elif not candidate_id and not company_id and name:
        sid = resolve_supplier_id(name)
        supplier_doc = db["suppliers"].find_one({"_id": sid}) if sid else None
    candidate_doc = db["external_supplier_candidates"].find_one({"_id": candidate_id}) if candidate_id else None
    if candidate_doc:
        name = name or candidate_doc.get("supplier_name") or candidate_doc.get("name") or ""
        company_id = company_id or candidate_doc.get("company_id")
        sid = sid or candidate_doc.get("supplier_id")
    if supplier_doc:
        company_id = company_id or supplier_doc.get("company_id")
        supplier_code = supplier_code or supplier_doc.get("supplier_code")
    if not name and not (sid or candidate_id or company_id):
        raise ValueError("监控对象缺少企业名称或稳定身份 ID")
    resolved_type = target_type or (
        "external_candidate" if candidate_id else
        "formal_supplier" if sid else "company"
    )
    if existing:
        name = name or existing.get("company_name", "")
        sid = sid or existing.get("supplier_id")
        candidate_id = candidate_id or existing.get("candidate_id")
        company_id = company_id or existing.get("company_id")
        supplier_code = supplier_code or existing.get("supplier_code")
        resolved_type = target_type or existing.get("target_type") or resolved_type
    target_id = monitor_target_id or (existing or {}).get("monitor_target_id") or str(uuid.uuid4())
    identity_status = "candidate" if resolved_type == "external_candidate" else (
        "verified" if sid or company_id else "unresolved"
    )
    target_doc = {
        "monitor_target_id": target_id,
        "target_type": resolved_type,
        "identity_status": identity_status,
        "company_name": name,
        "display_name": name,
        "supplier_id": sid,
        "candidate_id": candidate_id,
        "company_id": company_id,
        "supplier_code": supplier_code,
        "monitor_status": "active",
        "owner_user_id": owner_user_id or (existing or {}).get("owner_user_id"),
        "added_at": datetime.now(timezone.utc),
    }
    identity_filter = _target_query(
        monitor_target_id=monitor_target_id,
        supplier_id=sid,
        candidate_id=candidate_id,
        company_id=company_id,
        company_name=name or None,
    )
    db["watchlist"].update_one(
        identity_filter,
        {"$set": target_doc},
        upsert=True,
    )
    _broadcast_alert_update()
    return {**target_doc, "status": "watching"}


def remove_from_watchlist(
    company_name: str | None = None,
    *,
    monitor_target_id: str | None = None,
    supplier_id: str | None = None,
    candidate_id: str | None = None,
    company_id: str | None = None,
) -> dict:
    db = get_db()
    target = _find_watchlist_target(
        monitor_target_id=monitor_target_id,
        supplier_id=supplier_id,
        candidate_id=candidate_id,
        company_id=company_id,
        company_name=(company_name or "").strip() or None,
    )
    query = _target_query(
        monitor_target_id=monitor_target_id or (target or {}).get("monitor_target_id"),
        supplier_id=supplier_id,
        candidate_id=candidate_id,
        company_id=company_id,
        company_name=(company_name or "").strip() or None,
    )
    if not query:
        raise ValueError("移出监控缺少监控对象 ID 或企业名称")
    db["watchlist"].delete_one(query)
    _broadcast_alert_update()
    return {
        "monitor_target_id": (target or {}).get("monitor_target_id") or monitor_target_id,
        "company_name": (target or {}).get("company_name") or company_name,
        "target_type": (target or {}).get("target_type"),
        "supplier_id": (target or {}).get("supplier_id") or supplier_id,
        "candidate_id": (target or {}).get("candidate_id") or candidate_id,
        "company_id": (target or {}).get("company_id") or company_id,
        "status": "removed",
    }


def resolve_watchlist_identity(monitor_target_id: str, limit: int = 10) -> dict:
    """Resolve a monitoring target name to verified company candidates.

    This deliberately reuses the deterministic identity resolver used by sourcing.
    It returns candidates for a human decision and never changes the watchlist.
    """
    target = _find_watchlist_target(monitor_target_id=monitor_target_id)
    if target is None:
        raise ValueError("监控对象不存在")
    from app.domains.company.service import search_identity

    query = str(target.get("display_name") or target.get("company_name") or "").strip()
    if not query:
        return {
            "monitor_target_id": monitor_target_id,
            "query": query,
            "resolution": "pending_verification",
            "exact": None,
            "candidates": [],
        }
    result = search_identity(query, limit=max(1, min(limit, 20)))
    return {
        "monitor_target_id": monitor_target_id,
        "query": query,
        **result,
    }


def confirm_watchlist_identity(
    monitor_target_id: str,
    company_id: str,
    user_id: str,
    *,
    source_reference: str | None = None,
    comment: str | None = None,
) -> dict:
    """Bind a verified canonical company to a monitoring target after user confirmation."""
    target = _find_watchlist_target(monitor_target_id=monitor_target_id)
    if target is None:
        raise ValueError("监控对象不存在")

    from app.domains.company.service import get_company

    company = get_company(str(company_id))
    if company is None:
        raise ValueError("企业主体不存在")
    if company.get("verification_status") != "verified":
        raise ValueError("只能绑定已核验的企业主体")

    canonical_id = str(company.get("id") or company.get("company_id") or company_id)
    canonical_name = str(company.get("legal_name") or target.get("company_name") or "").strip()
    now = datetime.now(timezone.utc)
    db = get_db()
    confirmation = {
        "company_id": canonical_id,
        "confirmed_by": str(user_id),
        "confirmed_at": now,
        "source_reference": source_reference or company.get("source_reference"),
        "comment": comment,
    }
    db["watchlist"].update_one(
        {"monitor_target_id": monitor_target_id},
        {"$set": {
            "company_id": canonical_id,
            "company_name": canonical_name,
            "display_name": canonical_name,
            "identity_status": "verified",
            "identity_source": company.get("identity_source"),
            "identity_confirmation": confirmation,
            "updated_at": now,
        }},
    )
    _broadcast_alert_update()
    updated = _find_watchlist_target(monitor_target_id=monitor_target_id) or target
    return {
        **updated,
        "identity_confirmation": confirmation,
        "status": "verified",
    }


def get_watchlist() -> list[str]:
    return [target["company_name"] for target in get_watchlist_targets() if target.get("company_name")]


def refresh_company(company_name: str) -> dict:
    """Paid channel: call Tianyancha API, re-assess, compare."""
    from app.schemas import RiskAssessRequest
    from app.domains.risk.service import assess_risk
    from app.services.tianyancha_client import fetch_company

    fetch_company(company_name)
    assess_risk(RiskAssessRequest(company_name=company_name))
    return detect_changes(company_name)
