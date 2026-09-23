"""Provider-specific request options for OpenAI-compatible LLM APIs."""

from typing import Any

from app.core.config import settings


def is_minimax_m3() -> bool:
    """Return whether the configured model is MiniMax-M3."""
    return settings.LLM_MODEL.strip().lower() == "minimax-m3"


def completion_token_options(max_tokens: int) -> dict[str, Any]:
    """Build token-limit options compatible with the configured provider."""
    if is_minimax_m3():
        return {
            "max_completion_tokens": max_tokens,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    return {"max_tokens": max_tokens}
