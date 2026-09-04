"""LLM-first, validation-backed extraction for one conversation turn."""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger()

_ANALYSIS_TOKENS = (
    "风险", "财务", "商务", "供应依赖", "可替代", "质量", "交付",
    "ESG", "esg", "舆情", "合规", "制裁", "监控", "评估", "分析",
)


class ConversationIntentExtraction(BaseModel):
    """The narrow, side-effect-free contract the LLM may return."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_supplier_names: list[str] = Field(default_factory=list, max_length=10)
    analysis_dimensions: list[Literal[
        "risk", "financial", "business_risk", "quality", "delivery", "esg", "sentiment", "compliance"
    ]] = Field(
        default_factory=list,
        max_length=8,
    )
    requested_action: Literal["add_watchlist", "none"] = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("target_supplier_names")
    @classmethod
    def normalize_targets(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator("analysis_dimensions")
    @classmethod
    def normalize_dimensions(
        cls, value: list[Literal[
            "risk", "financial", "business_risk", "quality", "delivery", "esg", "sentiment", "compliance"
        ]]
    ) -> list[Literal[
        "risk", "financial", "business_risk", "quality", "delivery", "esg", "sentiment", "compliance"
    ]]:
        return list(dict.fromkeys(value))


def should_extract_conversation_intent(message: str) -> bool:
    """Avoid an LLM call for greetings and non-agent conversational turns."""
    return any(token in message for token in _ANALYSIS_TOKENS)


def extract_conversation_intent(
    message: str,
    supplier_references: list[dict[str, Any]],
) -> ConversationIntentExtraction | None:
    """Ask the LLM to understand the current turn; return None on safe fallback."""
    if not should_extract_conversation_intent(message) or not settings.LLM_API_KEY:
        return None

    prompt = {
        "task": "Extract the supplier-analysis intent from the current user message.",
        "current_message": message,
        "known_supplier_references": _reference_context(supplier_references),
        "response_schema": ConversationIntentExtraction.model_json_schema(),
        "rules": [
            "Return one JSON object only.",
            "Extract explicitly named companies from the current message even when they are absent from known_supplier_references.",
            "Use known_supplier_references only to resolve pronouns or aliases such as '这家' and '上述两家'.",
            "Do not invent companies, supplier codes, risk findings, or actions.",
            "Use requested_action='add_watchlist' only when the user explicitly asks to monitor or add to monitoring.",
            "This is read-only intent extraction and must not execute an action.",
        ],
    }
    try:
        client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=500,
            timeout=float(os.getenv("LLM_INTENT_EXTRACTION_TIMEOUT", "8")),
        )
        content = response.choices[0].message.content or "{}"
        extracted = ConversationIntentExtraction.model_validate(
            _normalize_llm_payload(json.loads(content))
        )
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        logger.warning("conversation_intent_extraction_invalid", error=str(exc))
        return None
    except Exception as exc:
        logger.warning("conversation_intent_extraction_failed", error=str(exc))
        return None

    validated = extracted.model_copy(update={
        "target_supplier_names": validate_extracted_targets(
            extracted.target_supplier_names, supplier_references
        )
    })
    logger.info(
        "conversation_intent_extracted",
        target_supplier_names=validated.target_supplier_names,
        analysis_dimensions=validated.analysis_dimensions,
        requested_action=validated.requested_action,
        confidence=validated.confidence,
    )
    return validated


def validate_extracted_targets(
    targets: list[str], supplier_references: list[dict[str, Any]]
) -> list[str]:
    """Canonicalize exact known names/aliases; keep unknown explicit names for lookup."""
    known_by_alias: dict[str, str] = {}
    for reference in supplier_references:
        if not isinstance(reference, dict):
            continue
        name = reference.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        canonical_name = name.strip()
        known_by_alias[canonical_name.casefold()] = canonical_name
        aliases = reference.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias.strip():
                    known_by_alias[alias.strip().casefold()] = canonical_name
    return list(dict.fromkeys(
        known_by_alias.get(target.strip().casefold(), target.strip())
        for target in targets
        if isinstance(target, str) and target.strip()
    ))


def _normalize_llm_payload(payload: Any) -> Any:
    """Accept Chinese dimension labels while retaining the strict public contract."""
    if not isinstance(payload, dict):
        return payload
    dimension_map = {
        "风险": "risk",
        "风险评估": "risk",
        "财务": "financial",
        "财务风险": "financial",
        "商务": "business_risk",
        "商务风险": "business_risk",
        "供应依赖": "business_risk",
        "可替代性": "business_risk",
        "质量": "quality",
        "质量风险": "quality",
        "交付": "delivery",
        "交付风险": "delivery",
        "esg": "esg",
        "ESG": "esg",
        "舆情": "sentiment",
        "合规": "compliance",
        "制裁": "compliance",
    }
    dimensions = payload.get("analysis_dimensions")
    return {
        **payload,
        **({
            "analysis_dimensions": [dimension_map.get(str(item), item) for item in dimensions]
        } if isinstance(dimensions, list) else {}),
    }


def _reference_context(supplier_references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": reference.get("name"),
            "aliases": reference.get("aliases", []),
            "supplier_code": reference.get("supplier_code"),
        }
        for reference in supplier_references
        if isinstance(reference, dict) and isinstance(reference.get("name"), str)
    ]
