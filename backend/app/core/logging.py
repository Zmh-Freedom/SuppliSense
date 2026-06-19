"""
Logging configuration with structlog.
"""

import logging
import os
import sys

import structlog


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structured logging."""
    log_format = os.getenv("LOG_FORMAT", "console")
    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    log_level_name = os.getenv("LOG_LEVEL", log_level)
    level = getattr(logging, log_level_name.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    # Also configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Get a structured logger."""
    return structlog.get_logger(name)
