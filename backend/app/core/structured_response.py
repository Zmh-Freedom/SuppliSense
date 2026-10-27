"""Parse provider JSON responses and log safe failure metadata."""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger()


class StructuredResponseError(ValueError):
    """A structured model response was empty, malformed, or not an object."""

    def __init__(self, kind: str):
        super().__init__(f"structured_response_{kind}")
        self.kind = kind


def _response_metadata(response: Any) -> dict[str, Any]:
    choices = getattr(response, "choices", None) or []
    choice = choices[0] if choices else None
    usage = getattr(response, "usage", None)
    return {
        "model": settings.LLM_MODEL,
        "finish_reason": getattr(choice, "finish_reason", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
    }


def parse_json_object_content(
    content: str | None,
    *,
    operation: str,
    response: Any = None,
) -> dict[str, Any]:
    """Decode JSON objects, including common fences and MiniMax wrappers.

    Logs only response shape metadata. Prompt and generated text are never logged.
    """
    raw = content or ""
    cleaned = raw.strip().removeprefix("\ufeff")
    metadata = _response_metadata(response)
    if not cleaned:
        logger.warning(
            "structured_response_invalid",
            operation=operation,
            reason="empty",
            content_length=len(raw),
            **metadata,
        )
        raise StructuredResponseError("empty")

    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        first = cleaned[0]
        leading_kind = "object" if first == "{" else "array" if first == "[" else "text"
        logger.warning(
            "structured_response_invalid",
            operation=operation,
            reason="malformed_json",
            content_length=len(raw),
            error_position=exc.pos,
            leading_kind=leading_kind,
            **metadata,
        )
        raise StructuredResponseError("malformed_json") from exc

    if not isinstance(payload, dict):
        logger.warning(
            "structured_response_invalid",
            operation=operation,
            reason="not_object",
            content_length=len(raw),
            **metadata,
        )
        raise StructuredResponseError("not_object")

    if payload.get("type") == "json_object":
        nested = payload.get("content")
        if isinstance(nested, dict):
            return nested
        if isinstance(nested, str):
            try:
                nested_payload = json.loads(nested)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "structured_response_invalid",
                    operation=operation,
                    reason="malformed_wrapper_content",
                    content_length=len(raw),
                    error_position=exc.pos,
                    **metadata,
                )
                raise StructuredResponseError("malformed_wrapper_content") from exc
            if isinstance(nested_payload, dict):
                return nested_payload
            raise StructuredResponseError("wrapper_content_not_object")
        # Some OpenAI-compatible providers put the JSON object fields beside
        # the wrapper marker instead of nesting them under `content`.
        return {key: value for key, value in payload.items() if key != "type"}

    return payload


__all__ = ["StructuredResponseError", "parse_json_object_content"]
