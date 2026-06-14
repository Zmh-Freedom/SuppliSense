"""
Authentication API routes.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.core.config import settings
from app.core.deps import get_current_active_user, require_admin
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.schemas.user import (
    Token,
    UserCreate,
    UserInDB,
    UserResponse,
    UserRole,
    UserUpdate,
)
from app.services.auth import (
    authenticate_user,
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    update_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


def _create_tokens(user) -> Token:
    """Create access + refresh token pair."""
    token_data = {"sub": user.id, "username": user.username, "role": user.role}
    access_token = create_access_token(
        data=token_data,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_token = create_refresh_token(data=token_data)
    return Token(access_token=access_token, refresh_token=refresh_token)


@router.post("/register", response_model=UserResponse)
async def register(
    user_data: UserCreate,
    current_user: UserInDB = Depends(require_admin),
):
    """Register a new user (admin only)."""
    try:
        user = create_user(user_data)
        return UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            role=user.role,
            created_at=user.created_at,
            is_active=user.is_active,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login and get access token."""
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用",
        )

    return _create_tokens(user)


@router.post("/login/json")
async def login_json(req: LoginRequest, response: Response):
    """Login with JSON body, sets HttpOnly cookie."""
    user = authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用",
        )

    tokens = _create_tokens(user)

    response.set_cookie(
        key="access_token",
        value=tokens.access_token,
        httponly=True,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )

    return {"detail": "登录成功", "username": user.username, "role": user.role}


@router.post("/refresh")
async def refresh_token(req: RefreshRequest, response: Response):
    """Exchange refresh token for a new access token."""
    payload = decode_token(req.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的刷新令牌")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的刷新令牌")

    user = get_user_by_id(user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")

    token_data = {"sub": user.id, "username": user.username, "role": user.role}
    access_token = create_access_token(
        data=token_data,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )

    return {"detail": "令牌已刷新"}


@router.post("/logout")
async def logout(response: Response):
    """Clear the HttpOnly cookie."""
    response.delete_cookie(key="access_token", path="/")
    return {"detail": "已退出登录"}


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: UserInDB = Depends(get_current_active_user),
):
    """Get current user information."""
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        role=current_user.role,
        created_at=current_user.created_at,
        is_active=current_user.is_active,
    )


@router.get("/users", response_model=list[UserResponse])
async def get_all_users(
    current_user: UserInDB = Depends(require_admin),
):
    """Get all users (admin only)."""
    users = list_users()
    return [
        UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            role=user.role,
            created_at=user.created_at,
            is_active=user.is_active,
        )
        for user in users
    ]


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user_endpoint(
    user_id: str,
    update_data: UserUpdate,
    current_user: UserInDB = Depends(require_admin),
):
    """Update a user (admin only)."""
    user = update_user(user_id, update_data)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        created_at=user.created_at,
        is_active=user.is_active,
    )


@router.delete("/users/{user_id}")
async def delete_user_endpoint(
    user_id: str,
    current_user: UserInDB = Depends(require_admin),
):
    """Delete a user (admin only)."""
    # Prevent deleting yourself
    if current_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能删除自己",
        )

    success = delete_user(user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    return {"status": "deleted"}
