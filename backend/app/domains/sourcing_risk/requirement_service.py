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
    try:
        candidate = _extract_candidate(raw_text, provided_values)
    except (TypeError, ValueError) as exc:
        return _repair_or_clarify(raw_text, provided_values, _errors_from_exception(exc))

    if _contains_multiple_categories(candidate.get("category")):
        return _clarification_for_fields(["category"], candidate)

    try:
        requirement = SourcingRequirement.model_validate(candidate)
    except ValidationError as exc:
        return _repair_or_clarify(raw_text, provided_values, exc.errors())

    return _validated_result(requirement)


def _repair_or_clarify(
    raw_text: str,
    provided: dict[str, Any],
    validation_errors: list[dict[str, Any]],
) -> dict:
    """Use the one permitted repair response, then report its actual violations."""
    try:
        candidate = _extract_candidate(
            raw_text,
            provided,
            validation_errors,
            repair=True,
        )
    except (TypeError, ValueError) as exc:
        return _clarification_for_errors(_errors_from_exception(exc), provided)

    if _contains_multiple_categories(candidate.get("category")):
        return _clarification_for_fields(["category"], candidate)

    try:
        requirement = SourcingRequirement.model_validate(candidate)
    except ValidationError as exc:
        return _clarification_for_errors(exc.errors(), candidate)

    return _validated_result(requirement)


def _extract_candidate(
    raw_text: str,
    provided: dict[str, Any],
    validation_errors: list[dict[str, Any]] | None = None,
    repair: bool = False,
) -> dict[str, Any]:
    extracted = (
        repair_requirement(raw_text, provided, validation_errors or [])
        if repair
        else extract_requirement(raw_text, provided)
    )
    if not isinstance(extracted, dict):
        raise TypeError("LLM requirement response must be a JSON object")
    return {**extracted, **provided}


def _validated_result(requirement: SourcingRequirement) -> dict:
    data = requirement.model_dump()
    missing = missing_requirement_fields(data)
    if missing:
        return {"status": "clarification_required", "missing": missing}
    return {"status": "ready", "requirement": data}


def _contains_multiple_categories(category: Any) -> bool:
    return isinstance(category, str) and any(
        separator in category for separator in MULTI_CATEGORY_SEPARATORS
    )


def _clarification_for_errors(
    validation_errors: list[dict[str, Any]], requirement: dict[str, Any]
) -> dict:
    fields = [
        str(error["loc"][0])
        for error in validation_errors
        if error.get("loc") and isinstance(error["loc"][0], str)
    ]
    return _clarification_for_fields(fields, requirement)


def _clarification_for_fields(fields: list[str], requirement: dict[str, Any]) -> dict:
    missing = list(dict.fromkeys(fields))
    for field in missing_requirement_fields(requirement):
        if field not in missing:
            missing.append(field)
    return {"status": "clarification_required", "missing": missing}


def _errors_from_exception(exc: Exception) -> list[dict[str, Any]]:
    """Represent unparseable adapter output without inventing a requirement field."""
    return [{"loc": (), "type": exc.__class__.__name__}]
