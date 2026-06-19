"""
Shared rate limiter instance using slowapi.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address as _get_remote_address

from app.core.config import settings


def get_remote_address(request):
    """获取真实客户端 IP，优先读取反向代理头。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return _get_remote_address(request)


limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT_GLOBAL])
