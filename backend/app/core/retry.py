"""工具调用重试与降级模块。

为外部 API 工具提供自动重试 + 优雅降级能力。
重试策略：指数退避 (1s, 2s)，最多 2 次重试。
降级策略：外部 API 失败后尝试从本地缓存/数据库获取数据。
"""

import functools
import time
from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger()

# 重试配置
MAX_RETRIES = 2
BASE_BACKOFF = 1.0  # seconds


def with_retry(
    max_retries: int = MAX_RETRIES,
    backoff: float = BASE_BACKOFF,
    degrade_fn: Callable[[], Any] | None = None,
    tool_name: str = "",
):
    """工具函数重试装饰器。

    捕获函数执行中的任何异常，按指数退避重试。
    全部重试失败后，调用 degrade_fn 降级（如果有），否则返回错误 dict。

    Args:
        max_retries: 最大重试次数（不含首次调用）
        backoff: 基础退避时间（秒）
        degrade_fn: 降级函数，无参数，返回降级结果
        tool_name: 工具名称（用于日志）

    Usage:
        @with_retry(max_retries=2, tool_name="search_company")
        def search_company(keyword: str):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            name = tool_name or func.__name__
            last_error: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries:
                        delay = backoff * (2**attempt)
                        logger.warning(
                            "tool_retry",
                            tool=name,
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            delay=delay,
                            error=str(e)[:200],
                        )
                        time.sleep(delay)
                        continue
                    break

            # 全部重试失败
            logger.error(
                "tool_all_retries_failed",
                tool=name,
                max_retries=max_retries,
                error=str(last_error),
            )

            # 尝试降级
            if degrade_fn is not None:
                try:
                    logger.info("tool_degrading", tool=name)
                    return degrade_fn()
                except Exception as deg_e:
                    logger.error(
                        "tool_degrade_failed",
                        tool=name,
                        error=str(deg_e),
                    )

            # 返回结构化错误
            return {
                "error": f"工具 {name} 执行失败（已重试 {max_retries} 次）",
                "detail": str(last_error)[:500] if last_error else "未知错误",
                "degraded": degrade_fn is not None,
            }

        return wrapper

    return decorator


def async_with_retry(
    max_retries: int = MAX_RETRIES,
    backoff: float = BASE_BACKOFF,
    degrade_fn: Callable[[], Any] | None = None,
    tool_name: str = "",
):
    """异步版本的重试装饰器。"""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            import asyncio

            name = tool_name or func.__name__
            last_error: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries:
                        delay = backoff * (2**attempt)
                        logger.warning(
                            "tool_retry",
                            tool=name,
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            delay=delay,
                            error=str(e)[:200],
                        )
                        await asyncio.sleep(delay)
                        continue
                    break

            logger.error(
                "tool_all_retries_failed",
                tool=name,
                max_retries=max_retries,
                error=str(last_error),
            )

            if degrade_fn is not None:
                try:
                    logger.info("tool_degrading", tool=name)
                    result = degrade_fn()
                    if asyncio.iscoroutine(result):
                        return await result
                    return result
                except Exception as deg_e:
                    logger.error("tool_degrade_failed", tool=name, error=str(deg_e))

            return {
                "error": f"工具 {name} 执行失败（已重试 {max_retries} 次）",
                "detail": str(last_error)[:500] if last_error else "未知错误",
                "degraded": degrade_fn is not None,
            }

        return wrapper

    return decorator
