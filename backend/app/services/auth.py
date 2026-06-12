"""
Authentication service.
"""

from datetime import datetime, timezone

from bson import ObjectId

from app.core.security import verify_password, get_password_hash, create_access_token
from app.db.mongo import get_db
from app.schemas.user import UserCreate, UserInDB, UserRole, UserUpdate


def create_user(user_data: UserCreate) -> UserInDB:
    """Create a new user."""
    db = get_db()

    # Check if username or email already exists
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


def authenticate_user(username: str, password: str) -> UserInDB | None:
    """Authenticate a user by username and password."""
    db = get_db()
    user_doc = db["users"].find_one({"username": username})

    if not user_doc:
        return None

    if not verify_password(password, user_doc["password_hash"]):
        return None

    user_doc["id"] = str(user_doc["_id"])
    user_doc.pop("_id", None)

    return UserInDB(**user_doc)


def get_user_by_id(user_id: str) -> UserInDB | None:
    """Get a user by ID."""
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


def get_user_by_username(username: str) -> UserInDB | None:
    """Get a user by username."""
    db = get_db()
    user_doc = db["users"].find_one({"username": username})

    if not user_doc:
        return None

    user_doc["id"] = str(user_doc["_id"])
    user_doc.pop("_id", None)

    return UserInDB(**user_doc)


def update_user(user_id: str, update_data: UserUpdate) -> UserInDB | None:
    """Update a user."""
    db = get_db()

    update_dict = {}
    if update_data.email is not None:
        update_dict["email"] = update_data.email
    if update_data.role is not None:
        update_dict["role"] = update_data.role.value
    if update_data.is_active is not None:
        update_dict["is_active"] = update_data.is_active

    if not update_dict:
        return get_user_by_id(user_id)

    try:
        db["users"].update_one(
            {"_id": ObjectId(user_id)},
            {"$set": update_dict}
        )
    except Exception:
        return None

    return get_user_by_id(user_id)


def list_users() -> list[UserInDB]:
    """List all users."""
    db = get_db()
    users = []

    for user_doc in db["users"].find():
        user_doc["id"] = str(user_doc["_id"])
        user_doc.pop("_id", None)
        users.append(UserInDB(**user_doc))

    return users


def delete_user(user_id: str) -> bool:
    """Delete a user."""
    db = get_db()
    try:
        result = db["users"].delete_one({"_id": ObjectId(user_id)})
        return result.deleted_count > 0
    except Exception:
        return False
