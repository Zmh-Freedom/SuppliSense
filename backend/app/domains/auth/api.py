"""
Authentication API routes.
"""

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.core.config import settings
from app.core.deps import get_current_active_user, require_admin
from app.core.rate_limit import limiter
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.domains.auth.audit import log_action
from app.schemas.user import (
    Token,
    UserCreate,
    UserInDB,
    UserResponse,
    UserRole,
    UserUpdate,
)
from app.domains.auth.service import (
    authenticate_user,
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    update_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _audit_request(
    action: str,
    request: Request,
    *,
    user_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict | None = None,
) -> None:
    await asyncio.to_thread(
        log_action,
        action,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str = ""


def _create_tokens(user) -> Token:
    """Create access + refresh token pair."""
    token_data = {"sub": user.id, "username": user.username, "role": user.role}
    access_token = create_access_token(
        data=token_data,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_token = create_refresh_token(data=token_data)
    return Token(access_token=access_token, refresh_token=refresh_token)


@router.post(
    "/register",
    response_model=UserResponse,
    summary="注册新用户（管理员操作）",
    description="由管理员创建新的系统用户，设置用户名、邮箱、密码和角色。",
    responses={
        400: {"description": "请求参数错误或用户名已存在"},
        401: {"description": "未认证"},
        403: {"description": "无管理员权限"},
        500: {"description": "服务器内部错误"},
    },
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def register(
    request: Request,
    user_data: UserCreate,
    current_user: UserInDB = Depends(require_admin),
):
    """Register a new user (admin only)."""
    try:
        user = await asyncio.to_thread(create_user, user_data)
        await _audit_request(
            "auth.user_created", request, user_id=current_user.id,
            resource_type="user", resource_id=user.id,
            details={"role": user.role.value if isinstance(user.role, UserRole) else user.role},
        )
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


@router.post(
    "/login",
    response_model=Token,
    summary="用户登录（OAuth2 表单）",
    description="使用用户名和密码登录，返回 access_token 和 refresh_token。适用于 OAuth2 标准表单提交。",
    responses={
        401: {"description": "用户名或密码错误"},
        403: {"description": "用户已被禁用"},
        500: {"description": "服务器内部错误"},
    },
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    """Login and get access token."""
    user = await asyncio.to_thread(authenticate_user, form_data.username, form_data.password)
    if not user:
        await _audit_request("auth.login_failed", request, resource_type="user", resource_id=form_data.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        await _audit_request("auth.login_denied", request, user_id=user.id, resource_type="user", resource_id=user.id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用",
        )

    tokens = _create_tokens(user)
    await _audit_request("auth.login", request, user_id=user.id, resource_type="user", resource_id=user.id)
    return tokens


@router.post(
    "/login/json",
    summary="用户登录（JSON）",
    description="使用 JSON 格式的用户名和密码登录，成功后设置 HttpOnly Cookie。适用于前后端分离的 Web 应用。",
    responses={
        401: {"description": "用户名或密码错误"},
        403: {"description": "用户已被禁用"},
        500: {"description": "服务器内部错误"},
    },
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def login_json(request: Request, req: LoginRequest, response: Response):
    """Login with JSON body, sets HttpOnly cookie."""
    user = await asyncio.to_thread(authenticate_user, req.username, req.password)
    if not user:
        await _audit_request("auth.login_failed", request, resource_type="user", resource_id=req.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    if not user.is_active:
        await _audit_request("auth.login_denied", request, user_id=user.id, resource_type="user", resource_id=user.id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用",
        )

    tokens = _create_tokens(user)
    await _audit_request("auth.login", request, user_id=user.id, resource_type="user", resource_id=user.id)

    response.set_cookie(
        key="access_token",
        value=tokens.access_token,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=tokens.refresh_token,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/api/v1/auth",
    )

    return {"detail": "登录成功", "username": user.username, "role": user.role}


@router.post(
    "/refresh",
    summary="刷新访问令牌",
    description="使用 refresh_token 换取新的 access_token，同时更新 HttpOnly Cookie。",
    responses={
        401: {"description": "无效的刷新令牌"},
        500: {"description": "服务器内部错误"},
    },
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def refresh_token(request: Request, req: RefreshRequest, response: Response):
    """Exchange refresh token for a new access token."""
    refresh_token = req.refresh_token.strip() or request.cookies.get("refresh_token", "")
    payload = decode_token(refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的刷新令牌")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的刷新令牌")

    user = await asyncio.to_thread(get_user_by_id, user_id)
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
        secure=settings.COOKIE_SECURE,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )

    return {"detail": "令牌已刷新"}


@router.post(
    "/logout",
    summary="退出登录",
    description="清除 HttpOnly Cookie 中的 access_token，完成登出。",
    responses={
        500: {"description": "服务器内部错误"},
    },
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def logout(request: Request, response: Response):
    """Clear the HttpOnly cookie."""
    response.delete_cookie(key="access_token", path="/", secure=settings.COOKIE_SECURE)
    response.delete_cookie(key="refresh_token", path="/api/v1/auth", secure=settings.COOKIE_SECURE)
    return {"detail": "已退出登录"}


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


@router.put(
    "/me/password",
    summary="修改密码",
    description="修改当前登录用户的密码，需要提供原密码进行验证。",
    responses={
        400: {"description": "原密码错误"},
        401: {"description": "未认证"},
        500: {"description": "服务器内部错误"},
    },
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def change_password(
    request: Request,
    req: PasswordChangeRequest,
    current_user: UserInDB = Depends(get_current_active_user),
):
    """Change current user's password."""
    from app.core.security import verify_password, get_password_hash
    from app.core.config import settings

    if not await asyncio.to_thread(verify_password, req.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")

    password_hash = await asyncio.to_thread(get_password_hash, req.new_password)
    if settings.USE_PG_USERS:
        from app.domains.auth.user_repo import update_user as pg_update_user
        await asyncio.to_thread(pg_update_user, current_user.id, password_hash=password_hash)
    else:
        from app.db.mongo import get_db
        from bson import ObjectId
        await asyncio.to_thread(
            lambda: get_db()["users"].update_one(
                {"_id": ObjectId(current_user.id)},
                {"$set": {"password_hash": password_hash}},
            )
        )
    await _audit_request(
        "auth.password_changed", request, user_id=current_user.id,
        resource_type="user", resource_id=current_user.id,
    )
    return {"detail": "密码已修改"}


@router.get(
    "/me",
    response_model=UserResponse,
    summary="获取当前用户信息",
    description="返回当前已认证用户的基本信息，包括用户名、邮箱、角色和账户状态。",
    responses={
        401: {"description": "未认证或令牌已过期"},
        500: {"description": "服务器内部错误"},
    },
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def get_current_user_info(
    request: Request,
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


@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="获取所有用户列表（管理员操作）",
    description="返回系统中所有用户的列表，仅管理员可调用。",
    responses={
        401: {"description": "未认证"},
        403: {"description": "无管理员权限"},
        500: {"description": "服务器内部错误"},
    },
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def get_all_users(
    request: Request,
    current_user: UserInDB = Depends(require_admin),
):
    """Get all users (admin only)."""
    users = await asyncio.to_thread(list_users)
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


@router.put(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="更新用户信息（管理员操作）",
    description="更新指定用户的角色、邮箱或激活状态，仅管理员可调用。",
    responses={
        400: {"description": "请求参数错误"},
        401: {"description": "未认证"},
        403: {"description": "无管理员权限"},
        404: {"description": "用户不存在"},
        500: {"description": "服务器内部错误"},
    },
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def update_user_endpoint(
    request: Request,
    user_id: str,
    update_data: UserUpdate,
    current_user: UserInDB = Depends(require_admin),
):
    """Update a user (admin only)."""
    user = await asyncio.to_thread(update_user, user_id, update_data)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    await _audit_request(
        "auth.user_updated", request, user_id=current_user.id,
        resource_type="user", resource_id=user_id,
        details={"fields": sorted(update_data.model_dump(exclude_unset=True))},
    )

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        created_at=user.created_at,
        is_active=user.is_active,
    )


@router.delete(
    "/users/{user_id}",
    summary="删除用户（管理员操作）",
    description="删除指定用户，不可删除自己。仅管理员可调用。",
    responses={
        400: {"description": "不能删除自己"},
        401: {"description": "未认证"},
        403: {"description": "无管理员权限"},
        404: {"description": "用户不存在"},
        500: {"description": "服务器内部错误"},
    },
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def delete_user_endpoint(
    request: Request,
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

    success = await asyncio.to_thread(delete_user, user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    await _audit_request(
        "auth.user_deleted", request, user_id=current_user.id,
        resource_type="user", resource_id=user_id,
    )

    return {"status": "deleted"}
