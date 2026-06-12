"""
Sentry integration for error tracking.
"""

import os

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration


def init_sentry() -> None:
    """Initialize Sentry SDK."""
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return

    environment = os.getenv("ENVIRONMENT", "development")

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=0.1 if environment == "production" else 1.0,
        profiles_sample_rate=0.1 if environment == "production" else 0.0,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        send_default_pii=False,
    )


def capture_exception(error: Exception) -> str | None:
    """Capture an exception to Sentry."""
    return sentry_sdk.capture_exception(error)


def capture_message(message: str, level: str = "info") -> str | None:
    """Capture a message to Sentry."""
    return sentry_sdk.capture_message(message, level=level)
