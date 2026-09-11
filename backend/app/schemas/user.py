"""
User schemas.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, EmailStr, field_validator


class UserRole(str, Enum):
    ADMIN = "admin"
    PURCHASER = "purchaser"
    # Legacy value kept so existing JWTs, fixtures, and imported records can
    # be read while accounts are migrated to the procurement terminology.
    ANALYST = "analyst"
    VIEWER = "viewer"


def is_purchaser_role(role: str | UserRole) -> bool:
    """Return whether a role represents a procurement operator."""
    value = role.value if isinstance(role, UserRole) else str(role)
    return value in {UserRole.PURCHASER.value, UserRole.ANALYST.value}


class UserBase(BaseModel):
    username: str
    email: str
    role: UserRole = UserRole.VIEWER


class UserCreate(UserBase):
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("密码长度至少为8个字符")
        if not any(c.isalpha() for c in v):
            raise ValueError("密码必须包含至少一个字母")
        if not any(c.isdigit() for c in v):
            raise ValueError("密码必须包含至少一个数字")
        return v


class UserUpdate(BaseModel):
    email: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) < 8:
            raise ValueError("密码长度至少为8个字符")
        if not any(c.isalpha() for c in v):
            raise ValueError("密码必须包含至少一个字母")
        if not any(c.isdigit() for c in v):
            raise ValueError("密码必须包含至少一个数字")
        return v


class UserInDB(UserBase):
    id: str
    password_hash: str
    created_at: datetime
    is_active: bool = True

    class Config:
        from_attributes = True


class UserResponse(UserBase):
    id: str
    created_at: datetime
    is_active: bool = True

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    refresh_token: str = ""
    token_type: str = "bearer"


class TokenData(BaseModel):
    username: str | None = None
    user_id: str | None = None
    role: str | None = None
