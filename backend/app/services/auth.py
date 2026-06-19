"""
Authentication service.
Supports MongoDB (legacy) and PostgreSQL (new) via USE_PG_USERS flag.
"""

from datetime import datetime, timezone

from bson import ObjectId

from app.core.config import settings
from app.core.security import verify_password, get_password_hash
from app.db.mongo import get_db
from app.schemas.user import UserCreate, UserInDB, UserRole, UserUpdate


def create_user(user_data: UserCreate) -> UserInDB:
    if settings.USE_PG_USERS:
        return _create_user_pg(user_data)
    return _create_user_mongo(user_data)


def authenticate_user(username: str, password: str) -> UserInDB | None:
    if settings.USE_PG_USERS:
        return _authenticate_user_pg(username, password)
    return _authenticate_user_mongo(username, password)


def get_user_by_id(user_id: str) -> UserInDB | None:
    if settings.USE_PG_USERS:
        user = _get_user_by_id_pg(user_id)
        if user:
            return user
        # Fallback: try MongoDB for JWT tokens issued before migration
        return _get_user_by_id_mongo(user_id)
    return _get_user_by_id_mongo(user_id)


def get_user_by_username(username: str) -> UserInDB | None:
    if settings.USE_PG_USERS:
        return _get_user_by_username_pg(username)
    return _get_user_by_username_mongo(username)


def update_user(user_id: str, update_data: UserUpdate) -> UserInDB | None:
    if settings.USE_PG_USERS:
        return _update_user_pg(user_id, update_data)
    return _update_user_mongo(user_id, update_data)


def list_users() -> list[UserInDB]:
    if settings.USE_PG_USERS:
        return _list_users_pg()
    return _list_users_mongo()


def delete_user(user_id: str) -> bool:
    if settings.USE_PG_USERS:
        return _delete_user_pg(user_id)
    return _delete_user_mongo(user_id)


# ---- PostgreSQL implementations ----

def _create_user_pg(user_data: UserCreate) -> UserInDB:
    from app.repositories.user_repo import create_user as pg_create_user, find_by_username, find_by_email

    if find_by_username(user_data.username) or find_by_email(user_data.email):
        raise ValueError("用户名或邮箱已存在")

    role = user_data.role.value if isinstance(user_data.role, UserRole) else user_data.role
    doc = pg_create_user(
        username=user_data.username,
        email=user_data.email,
        password_hash=get_password_hash(user_data.password),
        role=role,
    )
    return UserInDB(**doc)


def _authenticate_user_pg(username: str, password: str) -> UserInDB | None:
    from app.repositories.user_repo import find_by_username

    doc = find_by_username(username)
    if not doc:
        return None
    if not verify_password(password, doc["password_hash"]):
        return None
    return UserInDB(**doc)


def _get_user_by_id_pg(user_id: str) -> UserInDB | None:
    from app.repositories.user_repo import find_by_id

    doc = find_by_id(user_id)
    if not doc:
        return None
    return UserInDB(**doc)


def _get_user_by_username_pg(username: str) -> UserInDB | None:
    from app.repositories.user_repo import find_by_username

    doc = find_by_username(username)
    if not doc:
        return None
    return UserInDB(**doc)


def _update_user_pg(user_id: str, update_data: UserUpdate) -> UserInDB | None:
    from app.repositories.user_repo import update_user as pg_update, find_by_id

    fields = {}
    if update_data.email is not None:
        fields["email"] = update_data.email
    if update_data.role is not None:
        fields["role"] = update_data.role.value
    if update_data.is_active is not None:
        fields["is_active"] = update_data.is_active

    if not fields:
        doc = find_by_id(user_id)
        return UserInDB(**doc) if doc else None

    doc = pg_update(user_id, **fields)
    if not doc:
        return None
    return UserInDB(**doc)


def _list_users_pg() -> list[UserInDB]:
    from app.repositories.user_repo import find_all

    return [UserInDB(**doc) for doc in find_all()]


def _delete_user_pg(user_id: str) -> bool:
    from app.repositories.user_repo import delete_user as pg_delete

    return pg_delete(user_id)


# ---- MongoDB implementations (existing code) ----

def _create_user_mongo(user_data: UserCreate) -> UserInDB:
    db = get_db()

    existing = db["users"].find_one({
        "$or": [
            {"username": user_data.username},
            {"email": user_data.email},
        ]
    })
    if existing:
        raise ValueError("用户名或邮箱已存在")

    user_doc = {
        "username": user_data.username,
        "email": user_data.email,
        "password_hash": get_password_hash(user_data.password),
        "role": user_data.role.value if isinstance(user_data.role, UserRole) else user_data.role,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }

    result = db["users"].insert_one(user_doc)
    user_doc["id"] = str(result.inserted_id)
    user_doc.pop("_id", None)

    return UserInDB(**user_doc)


def _authenticate_user_mongo(username: str, password: str) -> UserInDB | None:
    db = get_db()
    user_doc = db["users"].find_one({"username": username})

    if not user_doc:
        return None

    if not verify_password(password, user_doc["password_hash"]):
        return None

    user_doc["id"] = str(user_doc["_id"])
    user_doc.pop("_id", None)

    return UserInDB(**user_doc)


def _get_user_by_id_mongo(user_id: str) -> UserInDB | None:
    db = get_db()
    try:
        user_doc = db["users"].find_one({"_id": ObjectId(user_id)})
    except Exception:
        return None

    if not user_doc:
        return None

    user_doc["id"] = str(user_doc["_id"])
    user_doc.pop("_id", None)

    return UserInDB(**user_doc)


def _get_user_by_username_mongo(username: str) -> UserInDB | None:
    db = get_db()
    user_doc = db["users"].find_one({"username": username})

    if not user_doc:
        return None

    user_doc["id"] = str(user_doc["_id"])
    user_doc.pop("_id", None)

    return UserInDB(**user_doc)


def _update_user_mongo(user_id: str, update_data: UserUpdate) -> UserInDB | None:
    db = get_db()

    update_dict = {}
    if update_data.email is not None:
        update_dict["email"] = update_data.email
    if update_data.role is not None:
        update_dict["role"] = update_data.role.value
    if update_data.is_active is not None:
        update_dict["is_active"] = update_data.is_active

    if not update_dict:
        return _get_user_by_id_mongo(user_id)

    try:
        db["users"].update_one(
            {"_id": ObjectId(user_id)},
            {"$set": update_dict}
        )
    except Exception:
        return None

    return _get_user_by_id_mongo(user_id)


def _list_users_mongo() -> list[UserInDB]:
    db = get_db()
    users = []

    for user_doc in db["users"].find():
        user_doc["id"] = str(user_doc["_id"])
        user_doc.pop("_id", None)
        users.append(UserInDB(**user_doc))

    return users


def _delete_user_mongo(user_id: str) -> bool:
    db = get_db()
    try:
        result = db["users"].delete_one({"_id": ObjectId(user_id)})
        return result.deleted_count > 0
    except Exception:
        return False
