"""Allowlisted diagnostic codes retained by transactional Outbox."""


DELIVERY_FAILED = "delivery_failed"
_ALLOWED_DELIVERY_ERRORS = frozenset(
    {
        "consumer_not_registered",
        "consumer_handler_failed",
        DELIVERY_FAILED,
    }
)


def sanitize_delivery_error(error: object) -> str:
    """Map untrusted current or historical error values to a fixed public-safe code."""
    code = error if isinstance(error, str) else ""
    return code if code in _ALLOWED_DELIVERY_ERRORS else DELIVERY_FAILED
