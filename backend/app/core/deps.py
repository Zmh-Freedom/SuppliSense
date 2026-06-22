"""
FastAPI dependencies for authentication.
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.core.security import decode_token
from app.schemas.user import UserInDB, UserRole
from app.domains.auth.service import get_user_by_id

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def _extract_token(request: Request, header_token: str | None) -> str | None:
    """Extract JWT from HttpOnly cookie first, then Authorization header."""
    token = request.cookies.get("access_token")
    if token:
        return token
    return header_token


def get_current_user(
    request: Request,
    header_token: str | None = Depends(oauth2_scheme),
) -> UserInDB:
    """Get the current authenticated user from JWT token (cookie or header)."""
    token = _extract_token(request, header_token)

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Refresh tokens cannot be used for API access
    if payload.get("type") == "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请使用 access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用",
        )

    return user


def get_current_active_user(
    current_user: UserInDB = Depends(get_current_user),
) -> UserInDB:
    """Get the current active user (alias for clarity)."""
    return current_user


def require_role(allowed_roles: list[UserRole]):
    """Dependency factory: require user to have one of the allowed roles."""
    def role_checker(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
        user_role = UserRole(current_user.role)
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要权限: {', '.join(r.value for r in allowed_roles)}",
            )
        return current_user
    return role_checker


# Convenient role checkers
require_admin = require_role([UserRole.ADMIN])
require_admin_or_analyst = require_role([UserRole.ADMIN, UserRole.ANALYST])
require_any_role = require_role([UserRole.ADMIN, UserRole.ANALYST, UserRole.VIEWER])
