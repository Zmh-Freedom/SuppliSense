"""Safe formatting for failure text retained by transactional Outbox."""

import re


MAX_DELIVERY_ERROR_LENGTH = 240
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(?P<label>api[_-]?key|authorization|password|passwd|secret|token)"
    r"\s*(?:=|:)\s*(?:bearer\s+)?[^\s,;]+"
)


def sanitize_delivery_error(error: object) -> str:
    """Redact, normalize, and bound untrusted exception text before retention."""
    text = _CONTROL_CHARACTERS.sub(" ", str(error))
    text = _WHITESPACE.sub(" ", text).strip()
    text = _SENSITIVE_VALUE.sub(lambda match: f"{match.group('label')}=[REDACTED]", text)
    if not text:
        return "处理失败"
    if len(text) <= MAX_DELIVERY_ERROR_LENGTH:
        return text
    return f"{text[:MAX_DELIVERY_ERROR_LENGTH - 1]}…"
