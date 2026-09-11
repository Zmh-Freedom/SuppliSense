"""
Notification API routes -- user notification center.
"""

from fastapi import APIRouter, Depends, Query, Request

from app.core.deps import get_current_active_user
from app.schemas.user import UserInDB

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _notification_scope(current_user: UserInDB) -> dict:
    """Build a fail-closed scope from the Feishu responsibility identity."""
    role = getattr(current_user.role, "value", current_user.role)
    if role == "admin":
        return {"purchaser_open_id": {"$exists": True, "$ne": None}}
    from app.domains.supplier.access import feishu_open_id_for_user

    open_id = feishu_open_id_for_user(str(current_user.id))
    if not open_id:
        return {"purchaser_open_id": "__unmapped_user__"}
    return {"$or": [{"purchaser_open_id": open_id}, {"manager_open_id": open_id}]}


@router.get(
    "",
    summary="获取通知列表",
    description="获取当前用户的通知列表，可按已读/未读状态筛选。",
    responses={401: {"description": "未认证"}, 500: {"description": "服务器内部错误"}},
)
async def list_notifications(
    request: Request,
    read: bool | None = Query(None, description="按已读状态筛选（None=全部）"),
    limit: int = Query(50, description="最大返回数"),
    current_user: UserInDB = Depends(get_current_active_user),
):
    """List notifications for current user."""
    from app.db.mongo import get_db

    db = get_db()
    query: dict = _notification_scope(current_user)
    if read is not None:
        query["read"] = read
    notifs = list(db["notifications"].find(query).sort("created_at", -1).limit(limit))
    for n in notifs:
        n["_id"] = str(n["_id"])
        if "created_at" in n:
            n["created_at"] = n["created_at"].isoformat()
    return {
        "notifications": notifs,
        "unread_count": db["notifications"].count_documents({**_notification_scope(current_user), "read": False}),
    }


@router.put(
    "/{notif_id}/read",
    summary="标记通知为已读",
    description="将指定通知标记为已读。",
    responses={401: {"description": "未认证"}, 500: {"description": "服务器内部错误"}},
)
async def mark_read(
    notif_id: str,
    current_user: UserInDB = Depends(get_current_active_user),
):
    """Mark a notification as read."""
    from bson import ObjectId

    from app.db.mongo import get_db

    db = get_db()
    try:
        object_id = ObjectId(notif_id)
    except Exception:
        return {"status": "ok"}
    db["notifications"].update_one(
        {"_id": object_id, **_notification_scope(current_user)}, {"$set": {"read": True}}
    )
    return {"status": "ok"}


@router.put(
    "/read-all",
    summary="全部标为已读",
    description="将所有未读通知标记为已读。",
    responses={401: {"description": "未认证"}, 500: {"description": "服务器内部错误"}},
)
async def mark_all_read(
    current_user: UserInDB = Depends(get_current_active_user),
):
    """Mark all notifications as read."""
    from app.db.mongo import get_db

    db = get_db()
    db["notifications"].update_many(
        {**_notification_scope(current_user), "read": False}, {"$set": {"read": True}}
    )
    return {"status": "ok"}
