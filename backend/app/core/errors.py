"""
Unified error response handlers.
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("app")


async def http_exception_handler(request: Request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": str(exc.detail),
                "detail": None,
            }
        },
    )


async def validation_exception_handler(request: Request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求参数校验失败",
                "detail": exc.errors(),
            }
        },
    )


async def unhandled_exception_handler(request: Request, exc):
    logger.exception("Unhandled exception: %s", str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
                "detail": None,
            }
        },
    )
