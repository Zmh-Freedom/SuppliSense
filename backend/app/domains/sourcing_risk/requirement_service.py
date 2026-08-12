"""Validated boundary for turning sourcing text into procurement requirements."""

import json
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from app.core.config import settings

REQUIRED_FIELDS = ("category", "specification")
MULTI_CATEGORY_SEPARATORS = ("、", ",", "，", "/")


class SourcingRequirement(BaseModel):
    """The only fields an LLM may extract from a sourcing request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: StrictStr | None = Field(default=None, max_length=128)
    specification: StrictStr | None = Field(default=None, max_length=2000)
    region: StrictStr | None = Field(default=None, max_length=256)
    quantity: StrictInt | None = Field(default=None, ge=1)
    budget: StrictStr | None = Field(default=None, max_length=256)
    qualifications: StrictStr | None = Field(default=None, max_length=2000)
    delivery: StrictStr | None = Field(default=None, max_length=2000)
    risk_limit: StrictStr | None = Field(default=None, max_length=256)
    candidate_count: StrictInt | None = Field(default=None, ge=1, le=20)


def extract_requirement(
    raw_text: str,
    provided: dict[str, Any] | None = None,
    validation_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ask the model for JSON limited to the requirement schema."""
    prompt = {
        "task": "Extract one procurement requirement from the user text.",
        "raw_text": raw_text,
        "provided": provided or {},
        "validation_errors": validation_errors or [],
        "allowed_fields": SourcingRequirement.model_json_schema(),
        "rules": [
            "Return a JSON object only.",
            "Do not infer supplier identity, scores, sanctions, or any decision.",
            "Do not write or request side effects.",
            "Use null when a field is absent.",
        ],
    }
    client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)
    response = client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=600,
        timeout=30,
    )
    content = response.choices[0].message.content
    return json.loads(content or "{}")


def repair_requirement(
    raw_text: str,
    provided: dict[str, Any] | None,
    validation_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Perform the sole allowed correction attempt for malformed extraction."""
    return extract_requirement(raw_text, provided, validation_errors)


def missing_requirement_fields(requirement: dict[str, Any]) -> list[str]:
    """Return the required fields absent from a partially parsed requirement."""
    return [field for field in REQUIRED_FIELDS if not requirement.get(field)]


def parse_requirement(raw_text: str, provided: dict | None = None) -> dict:
    """Extract, validate once, and route incomplete requirements to clarification."""
    provided_values = provided or {}
    extracted = extract_requirement(raw_text, provided_values)
    candidate = {**extracted, **provided_values}

    if _contains_multiple_categories(candidate.get("category")):
        return _clarification(candidate)

    try:
        requirement = SourcingRequirement.model_validate(candidate)
    except ValidationError as exc:
        repaired = repair_requirement(raw_text, provided_values, exc.errors())
        candidate = {**repaired, **provided_values}
        if _contains_multiple_categories(candidate.get("category")):
            return _clarification(candidate)
        try:
            requirement = SourcingRequirement.model_validate(candidate)
        except ValidationError:
            return _clarification(candidate)

    data = requirement.model_dump()
    missing = missing_requirement_fields(data)
    if missing:
        return {"status": "clarification_required", "missing": missing}
    return {"status": "ready", "requirement": data}


def _contains_multiple_categories(category: Any) -> bool:
    return isinstance(category, str) and any(
        separator in category for separator in MULTI_CATEGORY_SEPARATORS
    )


def _clarification(requirement: dict[str, Any]) -> dict:
    missing = missing_requirement_fields(requirement)
    if "category" not in missing:
        missing.insert(0, "category")
    return {"status": "clarification_required", "missing": missing}
