"""Sourcing repository — MongoDB CRUD for sourcing_requests and sourcing_results."""

import uuid
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.db.mongo import get_db
from app.schemas.documents import SourcingRequestDocument, SourcingResultDocument, AccessApplicationDocument


def _validate_sourcing_req(data: dict) -> dict:
    return SourcingRequestDocument(**data).model_dump()


def _validate_sourcing_result(data: dict) -> dict:
    return SourcingResultDocument(**data).model_dump()


def _validate_access_app(data: dict) -> dict:
    return AccessApplicationDocument(**data).model_dump()


def create_request(data: dict) -> str:
    validated = _validate_sourcing_req(data)
    db = get_db()
    rid = str(uuid.uuid4())
    doc = {
        "_id": rid,
        **validated,
        "created_at": datetime.now(timezone.utc),
        "completed_at": None,
    }
    db["sourcing_requests"].insert_one(doc)
    return rid


def get_request(rid: str) -> dict | None:
    db = get_db()
    return db["sourcing_requests"].find_one({"_id": rid})


def update_request_status(rid: str, status: str, result_count: int = 0) -> None:
    db = get_db()
    update: dict[str, Any] = {"status": status, "result_count": result_count}
    if status == "done":
        update["completed_at"] = datetime.now(timezone.utc)
    db["sourcing_requests"].update_one({"_id": rid}, {"$set": update})


def list_requests(
    user_id: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    db = get_db()
    filt: dict[str, Any] = {}
    if user_id:
        filt["user_id"] = user_id
    if status:
        filt["status"] = status

    total = db["sourcing_requests"].count_documents(filt)
    cursor = (
        db["sourcing_requests"]
        .find(filt)
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(cursor)
    for item in items:
        item["request_id"] = str(item["_id"])
        del item["_id"]

    return {"items": items, "total": total}


def save_result(result_id: str, data: dict) -> None:
    validated = _validate_sourcing_result(data)
    db = get_db()
    validated["_id"] = result_id
    validated["created_at"] = datetime.now(timezone.utc)
    db["sourcing_results"].insert_one(validated)


def get_results(request_id: str) -> list[dict]:
    db = get_db()
    cursor = (
        db["sourcing_results"]
        .find({"request_id": request_id})
        .sort("final_rank", -1)
        .limit(10)
    )
    results = list(cursor)
    for r in results:
        r["result_id"] = str(r["_id"])
        del r["_id"]
    return results


def get_result(result_id: str) -> dict | None:
    db = get_db()
    return db["sourcing_results"].find_one({"_id": result_id})


def update_result_action(result_id: str, action: str) -> None:
    db = get_db()
    db["sourcing_results"].update_one(
        {"_id": result_id},
        {"$set": {"selected": True, "action": action}},
    )


def create_access_application(supplier_name: str, request_id: str | None, applicant_id: str) -> str:
    from app.domains.sourcing.supplier_repo import resolve_supplier_id

    supplier_id = resolve_supplier_id(supplier_name, auto_create=True)
    validated = _validate_access_app({
        "supplier_name": supplier_name,
        "supplier_id": supplier_id,
        "request_id": request_id,
        "applicant_id": applicant_id,
    })
    db = get_db()
    aid = str(uuid.uuid4())
    validated["_id"] = aid
    validated["created_at"] = datetime.now(timezone.utc)
    db["access_applications"].insert_one(validated)
    return aid


def get_access_application(aid: str) -> dict | None:
    db = get_db()
    return db["access_applications"].find_one({"_id": aid})


def list_access_applications(
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    db = get_db()
    filt: dict[str, Any] = {}
    if status and status != "all":
        filt["status"] = status

    total = db["access_applications"].count_documents(filt)
    cursor = (
        db["access_applications"]
        .find(filt)
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(cursor)
    for item in items:
        item["application_id"] = str(item["_id"])
        del item["_id"]
        if item.get("created_at"):
            item["created_at"] = item["created_at"].isoformat()
        if item.get("reviewed_at"):
            item["reviewed_at"] = item["reviewed_at"].isoformat()

    return {"items": items, "total": total}


def approve_access_application(aid: str, reviewer_id: str) -> None:
    db = get_db()
    old = db["access_applications"].find_one({"_id": aid}) or {}
    try:
        db["access_applications"].update_one(
            {"_id": aid},
            {"$set": {
                "status": "approved",
                "reviewer_id": reviewer_id,
                "reviewed_at": datetime.now(timezone.utc),
            }},
        )
        _sync_supplier_status(aid, "approved")
    except Exception:
        if old.get("status"):
            db["access_applications"].update_one(
                {"_id": aid},
                {"$set": {"status": old["status"], "reviewer_id": old.get("reviewer_id"), "reviewed_at": old.get("reviewed_at")}},
            )
        raise


def reject_access_application(aid: str, reviewer_id: str) -> None:
    db = get_db()
    old = db["access_applications"].find_one({"_id": aid}) or {}
    try:
        db["access_applications"].update_one(
            {"_id": aid},
            {"$set": {
                "status": "rejected",
                "reviewer_id": reviewer_id,
                "reviewed_at": datetime.now(timezone.utc),
            }},
        )
        _sync_supplier_status(aid, "blocked")
    except Exception:
        if old.get("status"):
            db["access_applications"].update_one(
                {"_id": aid},
                {"$set": {"status": old["status"], "reviewer_id": old.get("reviewer_id"), "reviewed_at": old.get("reviewed_at")}},
            )
        raise


def _sync_supplier_status(application_id: str, new_status: str) -> None:
    """审批联动：根据准入申请结果更新供应商主库状态。"""
    db = get_db()
    app = db["access_applications"].find_one({"_id": application_id})
    if app and app.get("supplier_id"):
        db["suppliers"].update_one(
            {"_id": app["supplier_id"]},
            {"$set": {"status": new_status, "updated_at": datetime.now(timezone.utc)}},
        )


def ensure_indexes() -> None:
    db = get_db()
    db["sourcing_requests"].create_index([("user_id", 1), ("created_at", -1)])
    db["sourcing_requests"].create_index("status")
    db["sourcing_results"].create_index([("request_id", 1), ("final_rank", -1)])
    db["sourcing_results"].create_index("request_id")
    db["access_applications"].create_index([("status", 1), ("created_at", -1)])
